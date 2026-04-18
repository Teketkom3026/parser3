import { useEffect, useRef, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { api, wsUrl } from '../api/client';

export function TaskPage() {
  const { taskId = '' } = useParams();
  const [task, setTask] = useState<any>(null);
  const [sites, setSites] = useState<any[]>([]);
  const [contacts, setContacts] = useState<any[]>([]);
  const [err, setErr] = useState('');
  const wsRef = useRef<WebSocket | null>(null);

  const refresh = async () => {
    try {
      const r = await api.getTask(taskId);
      setTask(r.task);
      setSites(r.sites || []);
      if (r.task?.status === 'completed' || r.task?.status === 'failed') {
        const c = await api.getContacts(taskId);
        setContacts(c.contacts || []);
      }
    } catch (e: any) {
      setErr(String(e.message || e));
    }
  };

  useEffect(() => {
    refresh();
    const ws = new WebSocket(wsUrl(taskId));
    wsRef.current = ws;
    ws.onmessage = () => refresh();
    ws.onerror = () => {};
    const t = setInterval(refresh, 3000);
    return () => {
      clearInterval(t);
      ws.close();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [taskId]);

  if (err) return <div className="container"><div className="card" style={{ color: '#c53030' }}>{err}</div></div>;
  if (!task) return <div className="container">Загрузка…</div>;

  const pct = task.total_urls
    ? Math.round((task.processed_urls / task.total_urls) * 100)
    : 0;

  return (
    <div>
      <header>
        <Link to="/">parser3</Link>
      </header>
      <div className="container">
        <h1>
          Задача {task.id}{' '}
          <span className={`badge badge-${task.status}`}>{task.status}</span>
        </h1>
        <div className="card">
          <div className="flex" style={{ justifyContent: 'space-between' }}>
            <div>
              <div className="muted">Прогресс</div>
              <div>
                {task.processed_urls}/{task.total_urls} сайтов, {task.found_contacts || task.total_contacts || 0} контактов
              </div>
            </div>
            <div className="flex">
              {task.status === 'running' && (
                <button className="secondary" onClick={() => api.pauseTask(taskId)}>
                  Пауза
                </button>
              )}
              {task.status === 'paused' && (
                <button onClick={() => api.resumeTask(taskId)}>Продолжить</button>
              )}
              {(task.status === 'running' || task.status === 'paused') && (
                <button className="danger" onClick={() => api.cancelTask(taskId)}>
                  Отменить
                </button>
              )}
              {task.status === 'completed' && (task.output_file || task.result_path) && (
                <a href={api.downloadUrl(taskId)}>
                  <button>Скачать XLSX</button>
                </a>
              )}
            </div>
          </div>
          <div className="progress-bar" style={{ marginTop: 12 }}>
            <div className="progress-bar-fill" style={{ width: `${pct}%` }} />
          </div>
        </div>

        <h2>Сайты</h2>
        <div className="card">
          <table>
            <thead>
              <tr>
                <th>URL</th>
                <th>Статус</th>
                <th>Контактов</th>
                <th>Ошибка</th>
              </tr>
            </thead>
            <tbody>
              {sites.map((s) => (
                <tr key={s.id}>
                  <td style={{ wordBreak: 'break-all' }}>{s.url}</td>
                  <td>
                    <span className={`badge badge-${s.status}`}>{s.status}</span>
                  </td>
                  <td>{s.contacts_found ?? s.contacts_count ?? 0}</td>
                  <td className="muted" style={{ fontSize: 12 }}>
                    {s.error_message || s.error || ''}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        {!!contacts.length && (
          <>
            <h2>Контакты ({contacts.length})</h2>
            <div className="card" style={{ overflowX: 'auto' }}>
              <table>
                <thead>
                  <tr>
                    <th>Лист</th>
                    <th>ФИО</th>
                    <th>Должность</th>
                    <th>Компания</th>
                    <th>Email</th>
                    <th>Телефон</th>
                    <th>Сайт</th>
                  </tr>
                </thead>
                <tbody>
                  {contacts.slice(0, 500).map((c, i) => (
                    <tr key={i}>
                      <td>
                        <span className="muted">{c.sheet_name || c.sheet}</span>
                      </td>
                      <td>{c.full_name || c.fio_full || ''}</td>
                      <td>{c.position_canonical || c.position_raw || ''}</td>
                      <td>{c.company_name || ''}</td>
                      <td>{c.person_email || c.email || ''}</td>
                      <td>{c.person_phone || c.phone || ''}</td>
                      <td>
                        {c.page_url && (
                          <a className="link" href={c.page_url} target="_blank" rel="noreferrer">
                            ссылка
                          </a>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {contacts.length > 500 && (
                <div className="muted">Показаны первые 500. Полный список — в XLSX.</div>
              )}
            </div>
          </>
        )}
      </div>
    </div>
  );
}
