const _BASE_PATH = import.meta.env.VITE_BASE_PATH ?? '/parser3';
const BASE = `${_BASE_PATH}/api/v1`;

async function req<T = any>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(`${BASE}${path}`, init);
  if (!r.ok) {
    const text = await r.text().catch(() => '');
    throw new Error(`HTTP ${r.status}: ${text || r.statusText}`);
  }
  return r.json();
}

export const api = {
  listTasks: () => req<{ tasks: any[] }>('/tasks'),
  getTask: (id: string) => req<{ task: any; sites: any[] }>(`/tasks/${id}`),
  getContacts: (id: string) => req<{ contacts: any[] }>(`/tasks/${id}/contacts`),

  createTask: (
    urls: string[],
    mode: string = 'all_contacts',
    target_positions: string[] = []
  ) =>
    req<{ task_id: string }>('/tasks', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ urls, mode, target_positions }),
    }),

  uploadTask: async (
    file: File,
    mode: string = 'all_contacts',
    target_positions: string[] = []
  ) => {
    const fd = new FormData();
    fd.append('file', file);
    fd.append('mode', mode);
    if (target_positions.length) {
      fd.append('target_positions', JSON.stringify(target_positions));
    }
    const r = await fetch(`${BASE}/tasks/upload`, { method: 'POST', body: fd });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    return r.json() as Promise<{ task_id: string; urls_count: number }>;
  },

  pauseTask: (id: string) => req(`/tasks/${id}/pause`, { method: 'POST' }),
  resumeTask: (id: string) => req(`/tasks/${id}/resume`, { method: 'POST' }),
  cancelTask: (id: string) => req(`/tasks/${id}/cancel`, { method: 'POST' }),
  deleteTask: (id: string) => req(`/tasks/${id}`, { method: 'DELETE' }),
  downloadUrl: (id: string) => `${BASE}/tasks/${id}/download`,
};

export function wsUrl(taskId: string): string {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  return `${proto}://${location.host}${_BASE_PATH}/ws/${taskId}`;
}
