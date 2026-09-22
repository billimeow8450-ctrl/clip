function normalizeApiBaseUrl(value) {
  const raw = (value || '').trim().replace(/\/$/, '');
  if (!raw) return '';
  if (/^https?:\/\//i.test(raw)) return raw;
  return `https://${raw}`;
}

// Empty VITE_API_BASE_URL means same-origin (deployments that proxy /api).
// For local development, run_studio.sh / vite.config sets the default.
export const API_BASE_URL = normalizeApiBaseUrl(import.meta.env.VITE_API_BASE_URL)
  || (import.meta.env.DEV ? 'http://localhost:8000' : '');

export function resolveApiUrl(path) {
  if (!path) return '';
  if (path === '#' || path.startsWith('blob:') || path.startsWith('data:')) return path;
  if (/^https?:\/\//i.test(path)) return path;
  // Signed URLs (/api/files/x?exp=..&uid=..&sig=..) pass through with base prepended
  return `${API_BASE_URL}${path.startsWith('/') ? path : `/${path}`}`;
}

function getAuthHeaders() {
  const token = sessionStorage.getItem('clip_auth_token');
  const headers = {};
  if (token) {
    headers['Authorization'] = `Bearer ${token}`;
  }
  return headers;
}

async function request(endpoint, options = {}) {
  const url = resolveApiUrl(endpoint);
  const headers = { ...getAuthHeaders(), ...(options.headers || {}) };

  if (options.body && !(options.body instanceof FormData) && typeof options.body === 'object') {
    options.body = JSON.stringify(options.body);
    headers['Content-Type'] = 'application/json';
  }

  let response;
  try {
    response = await fetch(url, { ...options, headers });
  } catch {
    throw new Error('Cannot reach the Clip Studio API. Check that the backend is running.');
  }

  if (!response.ok) {
    if (response.status === 401) {
      sessionStorage.removeItem('clip_auth_token');
      sessionStorage.removeItem('clip_user');
    }
    const errorData = await response.json().catch(() => ({ detail: 'An error occurred' }));
    throw new Error(errorData.detail || `Request failed with status ${response.status}`);
  }

  return response.json();
}

export const api = {
  auth: {
    async register(data) {
      const res = await request('/api/auth/register', { method: 'POST', body: data });
      if (res.token) {
        sessionStorage.setItem('clip_auth_token', res.token);
        sessionStorage.setItem('clip_user', JSON.stringify(res.user));
      }
      return res;
    },
    async login(data) {
      const res = await request('/api/auth/login', { method: 'POST', body: data });
      if (res.token) {
        sessionStorage.setItem('clip_auth_token', res.token);
        sessionStorage.setItem('clip_user', JSON.stringify(res.user));
      }
      return res;
    },
    async getMe() {
      return request('/api/auth/me');
    },
    /**
     * Server-side logout: revokes the JWT on the backend (finding C4/M7), then
     * clears local session data. Local cleanup happens even if the network
     * call fails, so the user is always logged out locally.
     */
    async logout() {
      try {
        await request('/api/auth/logout', { method: 'POST' });
      } catch {
        // Token may already be expired/invalid; local cleanup still applies.
      }
      sessionStorage.removeItem('clip_auth_token');
      sessionStorage.removeItem('clip_user');
    },
    async forgotPassword(email) {
      return request('/api/auth/forgot-password', { method: 'POST', body: { email } });
    },
    async resetPassword(data) {
      return request('/api/auth/reset-password', { method: 'POST', body: data });
    },
    getCurrentUser() {
      const user = sessionStorage.getItem('clip_user');
      return user ? JSON.parse(user) : null;
    }
  },

  youtube: {
    async getMetadata(url) {
      return request('/api/youtube/metadata', { method: 'POST', body: { url } });
    }
  },

  editor: {
    async process(params) {
      return request('/api/editor/process', { method: 'POST', body: params });
    }
  },

  clipper: {
    async process(params) {
      return request('/api/clipper/process', { method: 'POST', body: params });
    }
  },

  transcript: {
    async process(params) {
      return request('/api/transcript/process', { method: 'POST', body: params });
    }
  },

  jobs: {
    async get(jobId) {
      return request(`/api/jobs/${jobId}`);
    },
    async list(params = {}) {
      const query = new URLSearchParams();
      if (params.limit) query.set('limit', params.limit);
      if (params.offset) query.set('offset', params.offset);
      const qs = query.toString();
      return request(`/api/jobs${qs ? `?${qs}` : ''}`);
    }
  },

  projects: {
    async list() {
      return request('/api/projects');
    }
  },

  clips: {
    async list() {
      return request('/api/clips');
    }
  },

  files: {
    async upload(file) {
      const formData = new FormData();
      formData.append('file', file);
      const res = await request('/api/upload', { method: 'POST', body: formData });
      // Backend now returns a signed, expiring download URL (`url`) plus the
      // bare id path (`url_base`). Prefer the signed URL for downloads.
      return res;
    }
  },

  admin: {
    async overview() {
      return request('/api/admin/overview');
    },
    async users() {
      return request('/api/admin/users');
    }
  }
};
