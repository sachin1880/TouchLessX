import { useEffect, useState } from 'react';
import { authStorage, appRoleStorage } from '../storage/authStorage';
import type { AppRole } from '../types';

export type BootstrapRoute = 'RoleSelect' | 'Login' | 'CameraDevice' | 'ViewerDashboard';

export function useBootstrap() {
  const [loading, setLoading] = useState(true);
  const [initialRoute, setInitialRoute] = useState<BootstrapRoute>('RoleSelect');
  const [role, setRole] = useState<AppRole | null>(null);

  useEffect(() => {
    (async () => {
      const storedRole = await appRoleStorage.getRole();
      const token = await authStorage.getToken();

      if (!storedRole) {
        setInitialRoute('RoleSelect');
      } else if (!token) {
        setRole(storedRole);
        setInitialRoute('Login');
      } else if (storedRole === 'CAMERA_DEVICE') {
        setInitialRoute('CameraDevice');
      } else {
        setInitialRoute('ViewerDashboard');
      }
      setLoading(false);
    })();
  }, []);

  return { loading, initialRoute, role };
}
