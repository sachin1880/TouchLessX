"""Custom gesture training: dataset, scikit-learn classifier, evaluation, model versions.

MediaPipe already finds the hand; what is trained here is only a small classifier that
names a hand *pose* from its 21 landmarks. The dataset is a CSV of normalised landmark
features (see `custom_gestures.landmark_features`) - no images or video are ever stored.

Dataset format (data/gesture_samples.csv): a header row, then one row per sample:
    label, f0, f1, ... f41      (21 landmarks x (x, y), wrist-relative, palm-scaled, upright)
"""
from __future__ import annotations

import csv
import json
import time
from pathlib import Path

import numpy as np

from .custom_gestures import CustomGestureError, CustomGestureStore, landmark_features

FEATURE_COUNT = 42
MIN_SAMPLES_TO_TRAIN = 10      # per gesture; below this a split can't be evaluated
RECOMMENDED_SAMPLES = 40       # per gesture, for a model worth trusting
MAX_IMBALANCE = 3.0            # largest / smallest gesture before a warning
KEPT_MODEL_VERSIONS = 5


class TrainingError(ValueError):
    """Raised when the dataset can't be trained on, or a model can't be loaded."""


class GestureDataset:
    def __init__(self, path: Path | None = None) -> None:
        self._path = path
        self._samples: dict[str, np.ndarray] = {}
        self._load()

    def _load(self) -> None:
        if self._path is None or not self._path.exists():
            return
        rows: dict[str, list[list[float]]] = {}
        try:
            with open(self._path, "r", encoding="utf-8", newline="") as f:
                reader = csv.reader(f)
                next(reader, None)  # header
                for row in reader:
                    if len(row) != FEATURE_COUNT + 1:
                        continue  # damaged line: skip it rather than refuse the whole file
                    try:
                        values = [float(v) for v in row[1:]]
                        label = CustomGestureStore.validate_name(row[0])
                    except (ValueError, CustomGestureError):
                        continue
                    if all(np.isfinite(values)):
                        rows.setdefault(label, []).append(values)
        except OSError:
            return
        self._samples = {label: np.asarray(values, dtype=np.float32) for label, values in rows.items()}

    def save(self) -> None:
        if self._path is None:
            return
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._path, "w", encoding="utf-8", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(["label"] + [f"f{i}" for i in range(FEATURE_COUNT)])
                for label, rows in self._samples.items():
                    for row in rows:
                        writer.writerow([label] + [f"{value:.4f}" for value in row])
        except OSError:
            pass

    def labels(self) -> list[str]:
        return list(self._samples)

    def count(self, label: str) -> int:
        return len(self._samples.get(label, ()))

    def total(self) -> int:
        return sum(len(rows) for rows in self._samples.values())

    def create(self, label: str) -> str:
        label = CustomGestureStore.validate_name(label)
        if label in self._samples:
            raise TrainingError(f"A gesture named '{label}' already exists.")
        self._samples[label] = np.zeros((0, FEATURE_COUNT), dtype=np.float32)
        self.save()
        return label

    def add_samples(self, label: str, landmark_sets: list[np.ndarray]) -> int:
        """Appends samples (raw 21x3 landmark arrays) to a gesture; returns its new count."""
        label = CustomGestureStore.validate_name(label)
        if not landmark_sets:
            return self.count(label)
        rows = np.stack([landmark_features(landmarks) for landmarks in landmark_sets]).astype(np.float32)
        existing = self._samples.get(label)
        self._samples[label] = rows if existing is None or len(existing) == 0 else np.concatenate([existing, rows])
        self.save()
        return len(self._samples[label])

    def rename(self, old: str, new: str) -> str:
        new = CustomGestureStore.validate_name(new)
        if new == old:
            return old
        if new in self._samples:
            raise TrainingError(f"A gesture named '{new}' already exists.")
        self._samples = {(new if label == old else label): rows for label, rows in self._samples.items()}
        self.save()
        return new

    def delete(self, label: str) -> None:
        if self._samples.pop(label, None) is not None:
            self.save()

    def clear_samples(self, label: str) -> None:
        if label in self._samples:
            self._samples[label] = np.zeros((0, FEATURE_COUNT), dtype=np.float32)
            self.save()

    def arrays(self) -> tuple[np.ndarray, np.ndarray]:
        usable = {label: rows for label, rows in self._samples.items() if len(rows)}
        if not usable:
            return np.zeros((0, FEATURE_COUNT), dtype=np.float32), np.zeros((0,), dtype=object)
        features = np.concatenate(list(usable.values()))
        labels = np.concatenate([np.full(len(rows), label, dtype=object) for label, rows in usable.items()])
        return features, labels

    def issues(self) -> list[str]:
        """Human-readable warnings about data that would make a poor model."""
        warnings = []
        counts = {label: len(rows) for label, rows in self._samples.items()}
        if not counts:
            return ["No gestures yet. Create one and record some samples."]
        for label, count in counts.items():
            if count < MIN_SAMPLES_TO_TRAIN:
                warnings.append(f"'{label}' has {count} samples - at least {MIN_SAMPLES_TO_TRAIN} are needed to train.")
            elif count < RECOMMENDED_SAMPLES:
                warnings.append(f"'{label}' has only {count} samples - {RECOMMENDED_SAMPLES} or more give a "
                                "more reliable model.")
        trainable = [count for count in counts.values() if count >= MIN_SAMPLES_TO_TRAIN]
        if len(trainable) >= 2 and max(trainable) > MAX_IMBALANCE * min(trainable):
            warnings.append("The gestures are unbalanced: one has more than three times the samples of "
                            "another, which biases the model towards it. Record more of the smaller ones.")
        if len(trainable) == 1:
            warnings.append("Only one gesture can be trained, so accuracy can't be measured against another. "
                            "Add a second gesture for a meaningful evaluation.")
        return warnings


def train(dataset: GestureDataset, validation_split: float = 0.25, seed: int = 0) -> dict:
    """Trains a k-nearest-neighbours classifier and evaluates it on a held-out split.

    Returns a bundle: {"model", "labels", "features", "targets", "report"}. The report
    is measured on samples the evaluated model never saw; the deployed model is then
    refitted on everything.
    """
    from sklearn.metrics import accuracy_score, confusion_matrix, precision_recall_fscore_support
    from sklearn.model_selection import train_test_split
    from sklearn.neighbors import KNeighborsClassifier

    labels = [label for label in dataset.labels() if dataset.count(label) >= MIN_SAMPLES_TO_TRAIN]
    if not labels:
        raise TrainingError(f"Record at least {MIN_SAMPLES_TO_TRAIN} samples of a gesture before training.")
    all_features, all_targets = dataset.arrays()
    keep = np.isin(all_targets, labels)
    features, targets = all_features[keep], all_targets[keep].astype(str)

    def fit(x: np.ndarray, y: np.ndarray):
        smallest = min(int(np.sum(y == label)) for label in np.unique(y))
        return KNeighborsClassifier(n_neighbors=max(1, min(5, smallest)), weights="distance").fit(x, y)

    report: dict = {
        "labels": labels,
        "samples": {label: dataset.count(label) for label in labels},
        "warnings": dataset.issues(),
        "accuracy": None, "precision": None, "recall": None, "per_label": {}, "confusion": None,
        "train_size": len(targets), "validation_size": 0,
    }
    if len(labels) >= 2:
        x_train, x_val, y_train, y_val = train_test_split(
            features, targets, test_size=validation_split, random_state=seed, stratify=targets)
        predicted = fit(x_train, y_train).predict(x_val)
        precision, recall, _, support = precision_recall_fscore_support(
            y_val, predicted, labels=labels, zero_division=0)
        report.update(
            accuracy=float(accuracy_score(y_val, predicted)),
            precision=float(np.mean(precision)), recall=float(np.mean(recall)),
            per_label={label: {"precision": float(precision[i]), "recall": float(recall[i]),
                               "validation_samples": int(support[i])} for i, label in enumerate(labels)},
            confusion=confusion_matrix(y_val, predicted, labels=labels).tolist(),
            train_size=len(y_train), validation_size=len(y_val),
        )
    return {"model": fit(features, targets), "labels": labels, "features": features, "targets": targets,
            "report": report}


class TrainedClassifier:
    """What the gesture engine consults for poses the built-in rules don't know.

    A classifier on its own always answers with one of its labels, even for a hand shape
    it was never shown. So the model's answer only counts if the pose is also
    geometrically close to that gesture's training samples; otherwise: no gesture.
    """

    def __init__(self, bundle: dict) -> None:
        self.model = bundle["model"]
        self.labels = list(bundle["labels"])
        self.report = bundle.get("report", {})
        self._gate = CustomGestureStore()
        targets = np.asarray(bundle["targets"]).astype(str)
        for label in self.labels:
            self._gate._samples[label] = np.asarray(bundle["features"], dtype=np.float32)[targets == label]

    def predict(self, landmarks: np.ndarray) -> str | None:
        near = self._gate.predict(landmarks)
        if near is None:
            return None
        label = str(self.model.predict(landmark_features(landmarks).reshape(1, -1))[0])
        return label if label == near else None


class ModelManager:
    """Saves each trained model as a numbered version and remembers which one is active,
    so a retraining that turns out worse can be reverted."""

    def __init__(self, folder: Path | None = None) -> None:
        self._folder = folder
        self.versions: list[dict] = []
        self.active: int | None = None
        self._memory: dict[int, dict] = {}  # used when there is no folder (tests)
        if folder is None:
            return
        try:
            with open(folder / "index.json", "r", encoding="utf-8") as f:
                index = json.load(f)
            self.versions = [v for v in index.get("versions", []) if isinstance(v, dict) and "version" in v]
            self.active = index.get("active")
        except (OSError, ValueError):
            pass

    def _write_index(self) -> None:
        if self._folder is None:
            return
        try:
            self._folder.mkdir(parents=True, exist_ok=True)
            with open(self._folder / "index.json", "w", encoding="utf-8") as f:
                json.dump({"active": self.active, "versions": self.versions}, f, indent=2)
        except OSError:
            pass

    def _file(self, version: int) -> Path:
        return self._folder / f"gesture_model_v{version}.joblib"

    def save(self, bundle: dict) -> int:
        """Stores a freshly trained bundle as the next version and makes it active."""
        import joblib

        version = max([v["version"] for v in self.versions], default=0) + 1
        if self._folder is None:
            self._memory[version] = bundle
        else:
            self._folder.mkdir(parents=True, exist_ok=True)
            joblib.dump(bundle, self._file(version))
        report = bundle["report"]
        self.versions.append({
            "version": version, "created": time.strftime("%Y-%m-%d %H:%M"), "labels": report["labels"],
            "samples": sum(report["samples"].values()), "accuracy": report["accuracy"],
            "precision": report["precision"], "recall": report["recall"],
        })
        self.active = version
        for old in self.versions[:-KEPT_MODEL_VERSIONS]:
            self._memory.pop(old["version"], None)
            if self._folder is not None:
                self._file(old["version"]).unlink(missing_ok=True)
        self.versions = self.versions[-KEPT_MODEL_VERSIONS:]
        self._write_index()
        return version

    def load(self, version: int) -> TrainedClassifier:
        import joblib

        try:
            # joblib files are pickles: only ever load the ones this app wrote itself.
            bundle = self._memory[version] if self._folder is None else joblib.load(self._file(version))
            return TrainedClassifier(bundle)
        except Exception as exc:
            raise TrainingError(f"Model version {version} could not be loaded: {exc}") from exc

    def load_active(self) -> TrainedClassifier | None:
        if self.active is None:
            return None
        try:
            return self.load(self.active)
        except TrainingError:
            return None

    def activate(self, version: int) -> TrainedClassifier:
        """Reverts to (or re-selects) a stored version."""
        if version not in [v["version"] for v in self.versions]:
            raise TrainingError(f"There is no model version {version}.")
        classifier = self.load(version)
        self.active = version
        self._write_index()
        return classifier
