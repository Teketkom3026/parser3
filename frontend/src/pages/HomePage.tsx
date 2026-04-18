import { useEffect, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { api } from '../api/client';

export function HomePage() {
  const nav = useNavigate();
  const [urls, setUrls] = useState('');
  const [mode, setMode] = useState('all_contacts');
  const [tasks, setTasks] = useState<any[]>([]);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');

  const refresh = async () => {
    try {
      const r = await api.listTasks();
      setTasks(r.tasks || []);
    } catch (e: any) {
      setErr(String(e.message || e));
    }
  };

  useEffect(() => {
    refresh();
    const t = setInterval(refresh, 5000);
    return () => clearInterval(t);
  }, []);

  const submit = async () => {
    const list = urls
      .split(/\r?\n/)
      .map((s) => s.trim())
      .filter((s) => s && !s.startsWith('#'))
      .map((s) => (s.startsWith('http') ? s : `https://${s}`));
    if (!list.length) {
      setErr('Введите хотя бы один URL');
      return;
    }
    setBusy(true);
    setErr('');
    try {
      const r = await api.createTask(list, mode);
      nav(`/tasks/${r.task_id}`);
    } catch (e: any) {
      setErr(String(e.message || e));
    } finally {
      setBusy(false);
    }
  };

  const upload = async (file: File) => {
    setBusy(true);
    setErr('');
    try {
      const r = await api.uploadTask(file, mode);
      nav(`/tasks/${r.task_id}`);
    } catch (e: any) {
      setErr(String(e.message || e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div>
      <header>
        <Link to="/">parser3</Link>
        <span style={{ float: 'right', fontSize: 13, opacity: 0.8 }}>
          Парсер контактов с ролями
        </span>
      </header>
      <div className="container">
        <h1>Новая задача</h1>
        <div className="card">
          <label className="label">Список URL (по одному на строку)</label>
          <textarea
            value={urls}
            onChange={(e) => setUrls(e.target.value)}
            placeholder={'example.com\nhttps://company.ru'}
          />
          <div className="row" style={{ marginTop: 12 }}>
            <div>
              <label className="label">Режим</label>
              <select value={mode} onChange={(e) => setMode(e.target.value)}>
                <option value="all_contacts">Все контакты</option>
                <option value="target_positions">По списку должностей</option>
              </select>
            </div>
            <div className="grow" />
            <label className="label" style={{ cursor: 'pointer' }}>
              <input
                type="file"
                accept=".csv,.txt"
                style={{ display: 'none' }}
                onChange={(e) => {
                  const f = e.target.files?.[0];
                  if (f) upload(f);
                }}
              />
              <span className="button secondary"
                    style={{ display: 'inline-block', padding: '10px 20px',
                             background: '#718096', color: 'white', borderRadius: 4 }}>
                Загрузить CSV/TXT
              </span>
            </label>
            <button onClick={submit} disabled={busy}>
              {busy ? 'Запуск…' : 'Запустить парсинг'}
            </button>
          </div>
          {err && <div style={{ color: '#c53030', marginTop: 8 }}>{err}</div>}
        </div>

        <h2>Задачи</h2>
        <div className="card">
          {!tasks.length && <div className="muted">Задач пока нет</div>}
          {!!tasks.length && (
            <table>
              <thead>
                <tr>
                  <th>ID</th>
                  <th>Статус</th>
                  <th>Сайты</th>
                  <th>Контакты</th>
                  <th>Создана</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {tasks.map((t) => (
                  <tr key={t.id}>
                    <td>
                      <Link className="link" to={`/tasks/${t.id}`}>
                        {t.id}
                      </Link>
                    </td>
                    <td>
                      <span className={`badge badge-${t.status}`}>{t.status}</span>
                    </td>
                    <td>
                      {t.processed_urls}/{t.total_urls}
                    </td>
                    <td>{t.found_contacts ?? t.total_contacts ?? 0}</td>
                    <td>
                      <span className="muted">{t.created_at}</span>
                    </td>
                    <td>
                      {(t.output_file || t.result_path) && t.status === 'completed' && (
                        <a className="link" href={api.downloadUrl(t.id)}>
                          XLSX
                        </a>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>
    </div>
  );
}
