import { Routes, Route } from 'react-router-dom';
import Layout from './components/Layout';
import Dashboard from './pages/Dashboard';
import EventReview from './pages/EventReview';
import EventDetail from './pages/EventDetail';
import MatchDetail from './pages/MatchDetail';
import About from './pages/About';
import Settings from './pages/Settings';

export default function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route path="/" element={<Dashboard />} />
        <Route path="/event-review" element={<EventReview />} />
        <Route path="/events/:id" element={<EventDetail />} />
        <Route path="/matches/:id" element={<MatchDetail />} />
        <Route path="/settings" element={<Settings />} />
        <Route path="/about" element={<About />} />
      </Route>
    </Routes>
  );
}
