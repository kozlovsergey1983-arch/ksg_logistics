"""Read contact workbooks without executing formulas or following external links."""
from datetime import date, datetime, timezone
from zipfile import ZipFile, BadZipFile
from openpyxl import load_workbook
import uuid

MAX_ROWS = 10000


def text(value):
    if value is None:
        return ''
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def read_contacts(stream):
    try:
        with ZipFile(stream) as archive:
            if sum(i.file_size for i in archive.infolist()) > 30 * 1024 * 1024:
                raise ValueError('Распакованный Excel превышает 30 МБ')
        stream.seek(0)
        book = load_workbook(stream, read_only=True, data_only=True, keep_links=False)
    except (BadZipFile, OSError, KeyError) as exc:
        raise ValueError('Не удалось прочитать Excel. Выбери файл .xlsx') from exc
    contacts = []
    try:
        found = False
        for sheet in book:
            rows = sheet.iter_rows(values_only=True)
            headers = [text(v) for v in next(rows, ())]
            if 'Компания' not in headers and 'Название' not in headers:
                continue
            found = True
            if len(set(h for h in headers if h)) != len([h for h in headers if h]):
                raise ValueError('На листе повторяются заголовки столбцов')
            for number, values in enumerate(rows, 2):
                if number > MAX_ROWS + 1:
                    raise ValueError('На листе допускается не больше 10000 строк')
                row = {h: text(v) for h, v in zip(headers, values) if h}
                if not any(text(v) for v in values):
                    continue
                if not (row.get('Компания') or row.get('Название')):
                    raise ValueError(f'Лист «{sheet.title}», строка {number}: нет названия компании')
                if any(len(v) > 5000 for v in row.values()):
                    raise ValueError(f'Строка {number}: текст в ячейке превышает 5000 символов')
                contacts.append(row)
                if len(contacts) > MAX_ROWS:
                    raise ValueError('В файле допускается не больше 10000 компаний')
        if not found:
            raise ValueError('В первой строке листа нужен столбец «Компания» или «Название»')
        if not contacts:
            raise ValueError('В файле нет компаний для импорта')
        return contacts
    finally:
        book.close()


def merge_contacts(data, rows):
    inns = {p.get('inn', '').strip() for p in data['partners'] if p.get('inn', '').strip()}
    names = {' '.join(p['name'].casefold().split()) for p in data['partners']}
    added, skipped, preview = 0, 0, []
    for row in rows:
        name = row.get('Компания') or row.get('Название')
        inn = row.get('ИНН', '')
        key = ' '.join(name.casefold().split())
        if (inn in inns if inn else key in names):
            skipped += 1
            continue
        pid = str(uuid.uuid4())
        details = '\n'.join(f'{k}: {v}' for k, v in row.items() if v and k != '№')
        if len(details) > 50000:
            raise ValueError('Слишком много текста в строке компании')
        data['partners'].append(dict(id=pid, name=name, inn=inn, role='Клиент',
            phone=row.get('Телефон', ''), email=row.get('Email', ''), contact=row.get('Контактное лицо', ''),
            city=row.get('Город / район', ''), industry=row.get('Отрасль', ''), website=row.get('Сайт', ''),
            checked='', result='', docs=[]))
        data['notes'].insert(0, dict(id=str(uuid.uuid4()), partner=pid,
            date=datetime.now(timezone.utc).isoformat(), text='Импорт из Excel\n' + details))
        inns.add(inn) if inn else None
        names.add(key)
        added += 1
        if len(preview) < 10:
            preview.append(dict(name=name, inn=inn, phone=row.get('Телефон', '')))
    return dict(added=added, skipped=skipped, total=len(rows), preview=preview)
