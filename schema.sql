PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS unit (
    id INTEGER PRIMARY KEY,
    code TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    address TEXT NOT NULL,
    format TEXT NOT NULL CHECK (format IN ('Сухой','Отапливаемый','Холодильный')),
    area INTEGER NOT NULL CHECK (area > 0),
    rate INTEGER NOT NULL CHECK (rate > 0),
    enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0,1))
);

CREATE TABLE IF NOT EXISTS request (
    id INTEGER PRIMARY KEY,
    unit_id INTEGER NOT NULL REFERENCES unit(id),
    company TEXT NOT NULL CHECK (length(trim(company)) BETWEEN 2 AND 100),
    start_on TEXT NOT NULL CHECK (date(start_on,'+0 days') IS start_on),
    end_on TEXT NOT NULL CHECK (date(end_on,'+0 days') IS end_on),
    daily_price INTEGER NOT NULL CHECK (daily_price > 0),
    total INTEGER NOT NULL CHECK (
        total = daily_price * (julianday(end_on) - julianday(start_on))
    ),
    status TEXT NOT NULL DEFAULT 'NEW' CHECK (status IN ('NEW','APPROVED','REJECTED')),
    CHECK (julianday(end_on)-julianday(start_on) BETWEEN 1 AND 365)
);

CREATE TABLE IF NOT EXISTS lease (
    id INTEGER PRIMARY KEY,
    request_id INTEGER NOT NULL UNIQUE REFERENCES request(id),
    status TEXT NOT NULL DEFAULT 'RESERVED'
        CHECK (status IN ('RESERVED','ACTIVE','CLOSED','CANCELLED'))
);
CREATE INDEX IF NOT EXISTS ix_request_period ON request(unit_id,start_on,end_on);
CREATE VIEW IF NOT EXISTS occupied AS
    SELECT l.id, r.unit_id, r.start_on, r.end_on
    FROM lease l JOIN request r ON r.id=l.request_id
    WHERE l.status <> 'CANCELLED';

CREATE TRIGGER IF NOT EXISTS request_initial_status BEFORE INSERT ON request
WHEN NEW.status <> 'NEW'
BEGIN
    SELECT RAISE(ABORT,'invalid initial request status');
END;
CREATE TRIGGER IF NOT EXISTS request_snapshot BEFORE UPDATE OF
    unit_id, company, start_on, end_on, daily_price, total ON request
BEGIN
    SELECT RAISE(ABORT,'immutable request snapshot');
END;
CREATE TRIGGER IF NOT EXISTS request_status BEFORE UPDATE OF status ON request
WHEN OLD.status <> 'NEW' OR NEW.status NOT IN ('APPROVED','REJECTED')
BEGIN
    SELECT RAISE(ABORT,'invalid request transition');
END;
CREATE TRIGGER IF NOT EXISTS approve_request AFTER UPDATE OF status ON request
WHEN NEW.status='APPROVED'
BEGIN
    INSERT INTO lease(request_id) VALUES(NEW.id);
END;
CREATE TRIGGER IF NOT EXISTS insert_lease BEFORE INSERT ON lease
BEGIN
    SELECT CASE WHEN NEW.status <> 'RESERVED' OR NOT EXISTS (
        SELECT 1 FROM request r JOIN unit u ON u.id=r.unit_id
        WHERE r.id=NEW.request_id AND r.status='APPROVED' AND u.enabled=1
    ) THEN RAISE(ABORT,'invalid reservation') END;
    SELECT CASE WHEN EXISTS (
        SELECT 1 FROM occupied o JOIN request r ON r.id=NEW.request_id
        WHERE o.unit_id=r.unit_id AND o.start_on<r.end_on AND r.start_on<o.end_on
    ) THEN RAISE(ABORT,'period overlaps') END;
END;
CREATE TRIGGER IF NOT EXISTS lease_reference BEFORE UPDATE OF request_id ON lease
BEGIN
    SELECT RAISE(ABORT,'immutable reservation reference');
END;
CREATE TRIGGER IF NOT EXISTS lease_status BEFORE UPDATE OF status ON lease
WHEN NOT (
    (OLD.status='RESERVED' AND NEW.status IN ('ACTIVE','CANCELLED')) OR
    (OLD.status='ACTIVE' AND NEW.status='CLOSED')
)
BEGIN
    SELECT RAISE(ABORT,'invalid reservation transition');
END;
