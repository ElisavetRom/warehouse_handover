import argparse
from pathlib import Path
import sqlite3

import core

STATUS = {
    'NEW': 'На рассмотрении',
    'APPROVED': 'Одобрена',
    'REJECTED': 'Отклонена',
    'RESERVED': 'Забронировано',
    'ACTIVE': 'Передано',
    'CLOSED': 'Возвращено',
    'CANCELLED': 'Отменено',
}
FORMATS = ('Сухой', 'Отапливаемый', 'Холодильный')
MENU = '''
Складской фонд
1. Показать каталог
2. Создать заявку и рассчитать стоимость
3. Показать журнал заявок
4. Одобрить или отклонить заявку
5. Изменить состояние брони
6. Добавить помещение
0. Выход
'''


def rubles(value):
    return f'{value:,}'.replace(',', ' ') + ' руб.'


def read_integer(prompt, default=None):
    value = input(prompt).strip()
    if not value and default is not None:
        return default
    try:
        return int(value)
    except ValueError:
        raise ValueError('Введите целое число.') from None


def read_period():
    start = input('Дата заезда (ГГГГ-ММ-ДД): ').strip()
    end = input('Дата выезда (ГГГГ-ММ-ДД): ').strip()
    return start, end


def read_format(allow_all=False):
    for index, name in enumerate(FORMATS, 1):
        print(f'{index}. {name}')
    if allow_all:
        print('0. Все типы')
    choice = read_integer('Тип помещения: ', 0 if allow_all else None)
    if allow_all and choice == 0:
        return ''
    if 1 <= choice <= len(FORMATS):
        return FORMATS[choice - 1]
    raise ValueError('Выберите номер типа из списка.')


def show_catalog(db_path):
    start, end = read_period()
    area = read_integer('Площадь от, м² (Enter — любая): ', 0)
    kind = read_format(allow_all=True)
    with core.connect(db_path) as db:
        units = core.catalog(db, start, end, area, kind)
    if not units:
        print('Помещения по заданным критериям не найдены.')
    for unit in units:
        availability = 'свободно' if unit['free'] else 'занято'
        print(
            f"{unit['id']}. {unit['code']} — {unit['name']}, {unit['address']} | "
            f"{unit['format']}, {unit['area']} м² | "
            f"{rubles(unit['rate'])}/м² в сутки | {availability}"
        )


def create_request(db_path):
    unit_id = read_integer('Номер помещения из каталога: ')
    company = input('Название организации: ').strip()
    start, end = read_period()
    with core.connect(db_path) as db:
        request_id = core.submit(db, unit_id, company, start, end)
        unit, days, _, _ = core.quote(db, unit_id, start, end)
        saved = db.execute(
            'SELECT daily_price, total FROM request WHERE id=?', (request_id,)
        ).fetchone()
    print(f"Заявка № {request_id} создана. Склад {unit['code']}.")
    print(f"Расчёт: {unit['area']} м² × {rubles(unit['rate'])}/м² в сутки × {days} сут.")
    print(f"Суточная плата: {rubles(saved['daily_price'])}")
    print(f"Итоговая стоимость: {rubles(saved['total'])}")
    print(f"Статус заявки: {STATUS['NEW']}")


def show_journal(db_path):
    with core.connect(db_path) as db:
        requests = core.journal(db)
    if not requests:
        print('Журнал заявок пуст.')
    for request in requests:
        print(
            f"Заявка № {request['id']} | {request['company']} | {request['code']} | "
            f"{request['start_on']} — {request['end_on']} | {rubles(request['total'])} | "
            f"{STATUS[request['status']]}"
        )
        if request['lease_id'] is not None:
            print(f"  Бронь № {request['lease_id']}: {STATUS[request['lease_status']]}")


def decide_request(db_path):
    request_id = read_integer('Номер заявки: ')
    decision = input('1 — одобрить, 2 — отклонить: ').strip()
    if decision not in ('1', '2'):
        raise ValueError('Выберите 1 или 2.')
    with core.connect(db_path) as db:
        lease_id = core.decide(db, request_id, decision == '1')
    print(f'Бронь № {lease_id} создана.' if lease_id is not None else 'Заявка отклонена.')


def update_lease(db_path):
    lease_id = read_integer('Номер брони: ')
    print('1. Передать помещение\n2. Отменить бронь\n3. Зафиксировать возврат')
    choices = {'1': 'activate', '2': 'cancel', '3': 'close'}
    action = choices.get(input('Действие: ').strip())
    if action is None:
        raise ValueError('Выберите номер действия из списка.')
    with core.connect(db_path) as db:
        core.move_lease(db, lease_id, action)
    print(f'Статус брони: {STATUS[core.ACTIONS[action][1]]}')


def add_unit(db_path):
    code = input('Код помещения: ')
    name = input('Название: ')
    address = input('Адрес: ')
    kind = read_format()
    area = read_integer('Площадь, м²: ')
    rate = read_integer('Ставка, руб./м² в сутки: ')
    with core.connect(db_path) as db:
        unit_id = core.register_unit(db, code, name, address, kind, area, rate)
    print(f'Помещение добавлено в каталог. Номер {unit_id}.')


def run(db_path):
    core.initialize(db_path)
    commands = {
        '1': show_catalog,
        '2': create_request,
        '3': show_journal,
        '4': decide_request,
        '5': update_lease,
        '6': add_unit,
    }
    while True:
        print(MENU)
        try:
            choice = input('Выберите действие: ').strip()
            if choice == '0':
                break
            command = commands.get(choice)
            if command is None:
                print('Выберите номер действия из меню.')
                continue
            command(db_path)
        except (EOFError, KeyboardInterrupt):
            print()
            break
        except ValueError as error:
            print(f'Ошибка: {error}')
        except sqlite3.IntegrityError:
            print('Запись отклонена. Проверьте уникальность кода и допустимость данных.')
        except sqlite3.OperationalError:
            print('Операция с базой не завершена. Повторите попытку.')


def main():
    parser = argparse.ArgumentParser(description='Консольный прототип аренды складов')
    parser.add_argument('--db', type=Path, default=Path(__file__).with_name('warehouse.sqlite3'))
    options = parser.parse_args()
    try:
        run(options.db)
    except sqlite3.Error as error:
        parser.exit(1, f'Не удалось открыть или подготовить базу: {error}\n')


if __name__ == '__main__':
    main()
