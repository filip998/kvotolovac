import { Routes, Route } from 'react-router-dom';
import Layout from './components/Layout';
import Dashboard from './pages/Dashboard';
import MatchDetail from './pages/MatchDetail';
import About from './pages/About';

export default function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route path="/" element={<Dashboard />} />
        <Route path="/matches/:id" element={<MatchDetail />} />
        <Route path="/about" element={<About />} />
      </Route>
    </Routes>
  );
}
