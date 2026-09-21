function normalizeApiBaseUrl(value) {
  const raw = (value || '').trim().replace(/\/$/, '');
  if (!raw) return 'http://localhost:8000';
  if (/^https?:\/\//i.test(raw)) return raw;
  return `https://${raw}`;
}

export const API_BASE_URL = normalizeApiBaseUrl(import.meta.env.VITE_API_BASE_URL);

export function resolveApiUrl(path) {
  if (!path) return '';
  if (path === '#' || path.startsWith('blob:') || path.startsWith('data:')) return path;
  if (/^https?:\/\//i.test(path)) return path;
  return `${API_BASE_URL}${path.startsWith('/') ? path : `/${path}`}`;
}

function getAuthHeaders() {
  const token = localStorage.getItem('clip_auth_token');
  const headers = { 'Content-Type': 'application/json' };
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
  } else if (options.body instanceof FormData) {
    delete headers['Content-Type'];
  }

  let response;
  try {
    response = await fetch(url, { ...options, headers });
  } catch {
    throw new Error('Cannot reach the Clip Studio API. Check that the backend is running.');
  }
  
  if (!response.ok) {
    if (response.status === 401) {
      localStorage.removeItem('clip_auth_token');
      localStorage.removeItem('clip_user');
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
        localStorage.setItem('clip_auth_token', res.token);
        localStorage.setItem('clip_user', JSON.stringify(res.user));
      }
      return res;
    },
    async login(data) {
      const res = await request('/api/auth/login', { method: 'POST', body: data });
      if (res.token) {
        localStorage.setItem('clip_auth_token', res.token);
        localStorage.setItem('clip_user', JSON.stringify(res.user));
      }
      return res;
    },
    async getMe() {
      return request('/api/auth/me');
    },
    async forgotPassword(email) {
      return request('/api/auth/forgot-password', { method: 'POST', body: { email } });
    },
    async resetPassword(data) {
      return request('/api/auth/reset-password', { method: 'POST', body: data });
    },
    logout() {
      localStorage.removeItem('clip_auth_token');
      localStorage.removeItem('clip_user');
    },
    getCurrentUser() {
      const user = localStorage.getItem('clip_user');
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
    async list() {
      return request('/api/jobs');
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
      return request('/api/upload', { method: 'POST', body: formData });
    }
  }
};
