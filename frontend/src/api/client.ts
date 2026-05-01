import axios from 'axios';

const client = axios.create({
  baseURL: '/api/v1',
  timeout: 10000,
  headers: {
    'Content-Type': 'application/json',
  },
});

function responseDetailMessage(detail: unknown): string | null {
  if (typeof detail === 'string') {
    return detail;
  }
  if (Array.isArray(detail)) {
    return detail
      .map((item) => {
        if (typeof item === 'string') {
          return item;
        }
        if (item && typeof item === 'object' && 'msg' in item) {
          return String(item.msg);
        }
        return null;
      })
      .filter((item): item is string => item != null && item.length > 0)
      .join(', ');
  }
  return null;
}

client.interceptors.response.use(
  (response) => response,
  (error: unknown) => {
    if (axios.isAxiosError(error)) {
      const detailMessage = responseDetailMessage(error.response?.data?.detail);
      return Promise.reject(
        new Error(detailMessage || error.message || 'API request failed')
      );
    }
    return Promise.reject(error);
  }
);

export default client;
