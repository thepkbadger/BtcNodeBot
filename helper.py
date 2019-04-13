from datetime import datetime, timezone
import os.path
from urllib.parse import urlparse, parse_qs


def parse_config(file):
    config = {"bottoken": "", "botwhitelist": [], "bototp": False, "lnhost": "127.0.0.1", "lnport": 10009, "lnnet": "mainnet", "lndir": "", "lncertpath": "", "lnadminmacaroonpath": ""}

    try:
        with open(file, "r") as conf_file:
            lines = conf_file.readlines()
            for line in lines:
                line = line.strip()
                if line == "" or line[0] == "#":
                    continue
                param = line.split("=")
                if len(param) < 2:
                    continue
                end_pos = param[1].find(" ")
                if end_pos < 0:
                    end_pos = len(param[1])
                value = param[1][:end_pos]
                if param[0] in ["bototp"] and (value == "1" or value.lower() == "true"):
                    config[param[0]] = True
                elif param[0] in ["bototp"] and (value == "0" or value.lower() == "false"):
                    config[param[0]] = False
                elif param[0] in ["lnport"]:
                    config[param[0]] = int(value)
                elif param[0] == "botwhitelist":
                    usernames = value.split(",")
                    for user in usernames:
                        if user != "":
                            config[param[0]].append(user.strip())
                else:
                    config[param[0]] = str(value)

        return config
    except Exception as e:
        logToFile("Exception at reading config file: " + str(e))
        return config


def update_config_whitelist(file, new_usernames):
    try:
        with open(file, "r") as conf_file:
            lines = conf_file.readlines()
            for idx, line in enumerate(lines):
                if line == "" or line[0] == "#":
                    continue
                if len(line) >= 12 and line[:12] == "botwhitelist":
                    line = line.strip()
                    param = line.split("=")
                    if len(param) == 1:
                        lines[idx] = param[0] + "=" + ",".join(new_usernames)
                    elif len(param) > 1:
                        end_pos = param[1].find(" ")
                        rem = ""
                        if end_pos > -1:
                            rem = param[1][end_pos:]
                        lines[idx] = param[0] + "=" + ",".join(new_usernames) + rem

                    if len(param) > 2:
                        for i in range(2, len(param)):
                            lines[idx] += "=" + param[i]
                    lines[idx] += "\n"

        with open(file, "w") as conf_file:
            conf_file.truncate()
            for line in lines:
                conf_file.write(line)

        return True
    except Exception as e:
        logToFile("Exception at editing config file: " + str(e))
        return False


def try_parsing_date(text):
    for fmt in ('%b %d, %Y', '%d. %b %Y', '%d. %b. %Y', '%Y-%m-%d', '%Y%m%d', '%d-%b-%Y'):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            pass
    return None

root_dir = os.path.dirname(os.path.abspath(__file__))


def logToFile(msg):
    with open(root_dir + "/nodebot.log", "a") as file:
        file.write(datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S") + " - " + str(msg) + "\n")


def formatAmount(amount_sat, unit):
    if unit == "BTC":
        s = str("%.8f" % (amount_sat / 100000000.0)).rstrip('0')
    elif unit == "mBTC":
        s = str("%.5f" % (amount_sat / 100000.0)).rstrip('0')
    elif unit == "bits":
        s = str("%.2f" % (amount_sat / 100.0)).rstrip('0')
    else:
        s = "{:,}".format(int(amount_sat))

    if s[-1:] == "." or s[-1:] == ",":
        s = s[:-1]
    return s + " " + unit


def build_menu(buttons, n_cols, header_buttons=None, footer_buttons=None):
    # build telegram menu buttons
    menu = [buttons[i:i + n_cols] for i in range(0, len(buttons), n_cols)]
    if header_buttons:
        menu.insert(0, header_buttons)
    if footer_buttons:
        menu.append(footer_buttons)
    return menu


def parse_bip21(uri):
    try:
        response = {}
        data = urlparse(uri)
        # The scheme component ("bitcoin:") is case-insensitive
        # The rest of the URI is case-sensitive, including the query parameter keys.
        if data.scheme.lower() == "bitcoin":
            bitcoinaddress = data.path
            bitcoinparams = parse_qs(data.query)
            if bitcoinaddress == "":
                return None
            response["address"] = bitcoinaddress

            # Variables which are prefixed with a req- are considered required. If a client does not implement any
            # variables which are prefixed with req-, it MUST consider the entire URI invalid. Any other variables
            # which are not implemented, but which are not prefixed with a req-, can be safely ignored.
            for key, value in bitcoinparams.items():
                if key == "amount" and len(bitcoinparams["amount"]) == 1:
                    # If an amount is provided, it MUST be specified in decimal BTC. All amounts MUST contain no commas
                    # and use a period (.) as the separating character to separate whole numbers and decimal fractions.
                    value[0] = value[0].replace(',', '.')
                    if value[0].count(".") <= 1:
                        response["amount_sat"] = int(float(value[0]) * 100000000)
                else:
                    if key[:4] == "req-":
                        return None
                    else:
                        response[key] = value
            return response

        return None
    except Exception as e:
        return None


def amount_parse(amount, unit):
    try:
        curr_unit = unit
        allowed_units = ["mBTC", "BTC", "bits", "sats"]  # more specific string first, mBTC before BTC

        for allowed_unit in allowed_units:
            pos = amount.lower().find(allowed_unit.lower())
            if pos > -1:
                amount = amount[:pos].replace(" ", "")  # remove all spaces
                curr_unit = allowed_unit
                break

        if ((amount.count(".") == 1 and amount.count(",") == 0) or
                (amount.count(".") == 0 and amount.count(",") == 0) or
                (amount.count(".") == 0 and amount.count(",") == 1)) and amount != "":

            amount = amount.replace(',', '.')
            amount_sat = 0
            if curr_unit == "BTC":
                amount_sat = int(float(amount) * 10**8)
            elif curr_unit == "mBTC":
                amount_sat = int(float(amount) * 10**5)
            elif curr_unit == "bits":
                amount_sat = int(float(amount) * 10**2)
            elif curr_unit == "sats":
                amount_sat = int(amount)

            if amount_sat > 0:
                return amount_sat, True

        return 0, False
    except Exception as e:
        return 0, False
