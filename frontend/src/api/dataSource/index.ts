import { httpDataSource } from './httpDataSource';
import { mockDataSource } from './mockDataSource';

const USE_MOCK = import.meta.env.VITE_USE_MOCK !== 'false';

export const dataSource = USE_MOCK ? mockDataSource : httpDataSource;
