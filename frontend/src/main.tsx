import React from 'react';
import ReactDOM from 'react-dom/client';
import { BrowserRouter, Routes, Route } from 'react-router-dom';
import { HomePage } from './pages/HomePage';
import { TaskPage } from './pages/TaskPage';
import './styles.css';

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <BrowserRouter basename="/parser3">
      <Routes>
        <Route path="/" element={<HomePage />} />
        <Route path="/tasks/:taskId" element={<TaskPage />} />
      </Routes>
    </BrowserRouter>
  </React.StrictMode>,
);
