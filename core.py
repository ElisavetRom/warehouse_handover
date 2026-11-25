from contextlib import contextmanager
from datetime import date
from pathlib import Path
import sqlite3

ROOT = Path(__file__).resolve().parent
SEEDS = [
    (1, 'WH-101', 'Северный терминал', 'Химки, Северный проезд, 8', 'Отапливаемый', 420, 36),
    (2, 'WH-204', 'Склад Восток', 'Балашиха, Промышленная улица, 12', 'Сухой', 250, 29),
    (3, 'WH-315', 'Холодный контур', 'Домодедово, Логистическая улица, 4', 'Холодильный', 180, 54),
    (4, 'WH-402', 'Логопарк Запад', 'Одинцово, Складской проезд, 5', 'Сухой', 660, 31),
]
ACTIONS = {'activate': ('RESERVED', 'ACTIVE'), 'close': ('ACTIVE', 'CLOSED'),
           'cancel': ('RESERVED', 'CANCELLED')}


@contextmanager
def connect(path):
    db = sqlite3.connect(path, timeout=5)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    try:
        with db:
            yield db
    finally:
        db.close()


def initialize(path):
    with connect(path) as db:
        db.executescript((ROOT / 'schema.sql').read_text(encoding='utf-8'))
        db.executemany('INSERT OR IGNORE INTO unit VALUES(?,?,?,?,?,?,?,1)', SEEDS)


def period(start, end):
    try:
        first, last = date.fromisoformat(start), date.fromisoformat(end)
        if first.isoformat() != start or last.isoformat() != end:
            raise ValueError
    except ValueError:
        raise ValueError('Введите даты в формате ГГГГ-ММ-ДД.') from None
    days = (last-first).days
    if not 1 <= days <= 365:
        raise ValueError('Период должен составлять от 1 до 365 суток.')
    return days


def get_unit(db, unit_id):
    unit = db.execute('SELECT * FROM unit WHERE id=? AND enabled=1', (unit_id,)).fetchone()
    if unit is None:
        raise ValueError('Помещение отсутствует в каталоге.')
    return unit


def conflict(db, unit_id, start, end):
    return db.execute(
        'SELECT 1 FROM occupied WHERE unit_id=? AND start_on<? AND ?<end_on LIMIT 1',
        (unit_id, end, start),
    ).fetchone() is not None


def catalog(db, start, end, minimum=0, kind=''):
    period(start, end)
    if minimum < 0:
        raise ValueError('Минимальная площадь не может быть отрицательной.')
    return db.execute(
        '''SELECT u.*, NOT EXISTS (
             SELECT 1 FROM occupied o WHERE o.unit_id=u.id AND o.start_on<? AND ?<o.end_on
           ) AS free
           FROM unit u WHERE u.enabled=1 AND u.area>=? AND (?='' OR u.format=?)
           ORDER BY u.area,u.id''', (end, start, minimum, kind, kind)
    ).fetchall()


def quote(db, unit_id, start, end):
    days = period(start, end)
    unit = get_unit(db, unit_id)
    daily = unit['area'] * unit['rate']
    return unit, days, daily, daily * days


def submit(db, unit_id, company, start, end):
    db.execute('BEGIN IMMEDIATE')
    _, _, daily, total = quote(db, unit_id, start, end)
    company = company.strip()
    if not 2 <= len(company) <= 100:
        raise ValueError('Укажите название организации длиной от 2 до 100 символов.')
    if conflict(db, unit_id, start, end):
        raise ValueError('Помещение занято на выбранные даты.')
    return db.execute(
        'INSERT INTO request(unit_id,company,start_on,end_on,daily_price,total) VALUES(?,?,?,?,?,?)',
        (unit_id, company, start, end, daily, total),
    ).lastrowid


def decide(db, request_id, approve):
    db.execute('BEGIN IMMEDIATE')
    request = db.execute('SELECT * FROM request WHERE id=? AND status=\'NEW\'', (request_id,)).fetchone()
    if request is None:
        raise ValueError('Заявка уже обработана или отсутствует.')
    if approve and conflict(db, request['unit_id'], request['start_on'], request['end_on']):
        raise ValueError('Период уже занят. Заявка осталась на рассмотрении.')
    db.execute('UPDATE request SET status=? WHERE id=?',
               ('APPROVED' if approve else 'REJECTED', request_id))
    if approve:
        return db.execute('SELECT id FROM lease WHERE request_id=?', (request_id,)).fetchone()[0]
    return None


def move_lease(db, lease_id, action):
    source, target = ACTIONS[action]
    result = db.execute('UPDATE lease SET status=? WHERE id=? AND status=?', (target, lease_id, source))
    if result.rowcount != 1:
        raise ValueError('Действие недоступно для текущего состояния брони.')


def register_unit(db, code, name, address, kind, area, rate):
    code, name, address = code.strip().upper(), name.strip(), address.strip()
    if not code or not name or not address:
        raise ValueError('Заполните код, название и адрес помещения.')
    if not 1 <= area <= 100_000 or not 1 <= rate <= 100_000:
        raise ValueError('Площадь и ставка должны быть от 1 до 100 000.')
    return db.execute(
        'INSERT INTO unit(code,name,address,format,area,rate) VALUES(?,?,?,?,?,?)',
        (code, name, address, kind, area, rate),
    ).lastrowid


def journal(db):
    return db.execute(
        '''SELECT r.*,u.code,u.name,l.id AS lease_id,l.status AS lease_status
           FROM request r JOIN unit u ON u.id=r.unit_id
           LEFT JOIN lease l ON l.request_id=r.id ORDER BY r.id DESC'''
    ).fetchall()
