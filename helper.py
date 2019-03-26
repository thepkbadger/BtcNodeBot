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


def parameter_split(text, valid_flags, split_char_flag='=', split_char_text=' '):

    flags = []
    values = []

    if '"' in text:
        first_space = text.find(split_char_text)
        text = text[first_space+1:]

        state = 0
        curr_flag = ""
        curr_value = ""
        for c in text:
            if c == "=" and state == 0:
                if curr_flag in valid_flags:
                    flags.append(curr_flag)
                    curr_flag = ""
                else:
                    return False, [], []
                state = 1
            elif state == 0 and c != " ":
                curr_flag += c
            elif state == 1 and c == '"':
                state = 2
            elif (state == 2 and c == '"') or (state == 1 and c == " "):
                state = 0
                values.append(curr_value)
                curr_value = ""
            elif state == 2 or state == 1:
                curr_value += c

        if curr_flag != "":
            flags.append(curr_flag)
        if curr_value != "":
            values.append(curr_value)

    else:
        params = text.split(split_char_text)
        if len(params) > 0 and params[0][:1] == "/":
            params = params[1:]

        for p in params:
            i = p.find(split_char_flag)
            if i > -1 and p[0:i] in valid_flags:
                flags.append(p[0:i])
                values.append(p[i + 1:])
            else:
                return False, [], []

    if len(flags) != len(values):
        return False, [], []
    return True, flags, values

