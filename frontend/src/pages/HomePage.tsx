import { useEffect, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { api } from '../api/client';

const POSITION_OPTIONS: { key: string; label: string; keywords: string[] }[] = [
  { key: 'ceo',       label: 'Генеральные директора', keywords: ['генеральный директор', 'президент', 'исполнительный директор'] },
  { key: 'cfo',       label: 'Финансовые директора',  keywords: ['финансовый директор'] },
  { key: 'chief_acc', label: 'Главные бухгалтеры',    keywords: ['главный бухгалтер'] },
  { key: 'chief_eng', label: 'Главные инженеры',      keywords: ['главный инженер'] },
];

export function HomePage() {
  const nav = useNavigate();
  const [urls, setUrls] = useState('');
  const [mode, setMode] = useState('all_contacts');
  const [selectedKeys, setSelectedKeys] = useState<string[]>([]);
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

  const toggleKey = (k: string) => {
    setSelectedKeys((prev) =>
      prev.includes(k) ? prev.filter((x) => x !== k) : [...prev, k]
    );
  };

  const collectTargetPositions = (): string[] => {
    if (mode !== 'target_positions') return [];
    const acc: string[] = [];
    for (const opt of POSITION_OPTIONS) {
      if (selectedKeys.includes(opt.key)) acc.push(...opt.keywords);
    }
    return acc;
  };

  const validateBeforeSubmit = (): string | null => {
    if (mode === 'target_positions' && selectedKeys.length === 0) {
      return 'Выберите хотя бы одну должность';
    }
    return null;
  };

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
    const v = validateBeforeSubmit();
    if (v) { setErr(v); return; }
    setBusy(true);
    setErr('');
    try {
      const r = await api.createTask(list, mode, collectTargetPositions());
      nav(`/tasks/${r.task_id}`);
    } catch (e: any) {
      setErr(String(e.message || e));
    } finally {
      setBusy(false);
    }
  };

  const upload = async (file: File) => {
    const v = validateBeforeSubmit();
    if (v) { setErr(v); return; }
    setBusy(true);
    setErr('');
    try {
      const r = await api.uploadTask(file, mode, collectTargetPositions());
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
              <span className="button secondary" style={{ display: 'inline-block', padding: '10px 20px', background: '#718096', color: 'white', borderRadius: 4 }}>
                Загрузить CSV/TXT
              </span>
            </label>
            <button onClick={submit} disabled={busy}>
              {busy ? 'Запуск…' : 'Запустить парсинг'}
            </button>
          </div>

          {mode === 'target_positions' && (
            <div style={{ marginTop: 12, padding: 12, background: '#f7fafc', borderRadius: 4, border: '1px solid #e2e8f0' }}>
              <div className="label" style={{ marginBottom: 8 }}>
                Должности (отметьте нужные):
              </div>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 12 }}>
                {POSITION_OPTIONS.map((opt) => (
                  <label key={opt.key} style={{ display: 'flex', alignItems: 'center', gap: 6, cursor: 'pointer' }}>
                    <input
                      type="checkbox"
                      checked={selectedKeys.includes(opt.key)}
                      onChange={() => toggleKey(opt.key)}
                    />
                    <span>{opt.label}</span>
                  </label>
                ))}
              </div>
              {selectedKeys.length === 0 && (
                <div className="muted" style={{ marginTop: 8, fontSize: 12 }}>
                  Не выбрано ни одной должности — парсер не запустится.
                </div>
              )}
            </div>
          )}

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
                    <td><Link className="link" to={`/tasks/${t.id}`}>{t.id}</Link></td>
                    <td><span className={`badge badge-${t.status}`}>{t.status}</span></td>
                    <td>{t.processed_urls}/{t.total_urls}</td>
                    <td>{t.found_contacts ?? t.total_contacts ?? 0}</td>
                    <td><span className="muted">{t.created_at}</span></td>
                    <td>
                      {(t.output_file || t.result_path) && t.status === 'completed' && (
                        <a className="link" href={api.downloadUrl(t.id)}>XLSX</a>
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
