from datetime import datetime, timezone
import os.path


def try_parsing_date(text):
    for fmt in ('%b %d, %Y', '%d. %b %Y', '%d. %b. %Y', '%Y-%m-%d', '%Y%m%d', '%d-%b-%Y'):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            pass
    return None

root_dir = os.path.dirname(os.path.abspath(__file__))


def logToFile(msg):
    with open(root_dir + "/logs.txt", "a") as file:
        file.write(datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S") + " - " + str(msg) + "\n")


def build_menu(buttons, n_cols, header_buttons=None, footer_buttons=None):
    # build telegram menu buttons
    menu = [buttons[i:i + n_cols] for i in range(0, len(buttons), n_cols)]
    if header_buttons:
        menu.insert(0, header_buttons)
    if footer_buttons:
        menu.append(footer_buttons)
    return menu


def amount_parse(amount):
    try:
        if amount.count(".") == 1 or amount.count(",") == 1:
            amount = amount.replace(',', '.')
            value = int(float(amount) * 100000000)
            if value > 0:
                return value, True
        elif amount.count(".") == 0 and amount.count(",") == 0:
            value = int(amount)
            if value > 0:
                return value, True
        return 0, False
    except Exception as e:
        return 0, False
