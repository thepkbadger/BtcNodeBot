import os
import json


class UserData:

    root_dir = os.path.dirname(os.path.abspath(__file__))
    data = {}
    default_data = {
        "wallet": {
            "invoice": None,
            "notifications": {"node": True, "transactions": True, "invoices": True, "chevents": False},
            "default_explorer_tx": "https://blockstream.info/tx/",
            "selected_unit": "sats",
            "onchain_send_data": {"amount": 0, "address": "", "sat_per_byte": -1, "target_conf": -1},
            "add_invoice_data": {"amount": 0, "expiry": 3600, "description": ""},
            "open_channel_data": {"address": "", "local_amount": 0, "target_conf": -1, "sat_per_byte": -1, "private": False, "min_htlc_msat": 1000, "remote_csv_delay": -1},
            "close_channel_data": {"chan_id": "", "target_conf": -1, "sat_per_byte": -1}
        },
        "chat_id": None,
        "conversation_state": None,
        "pagination_number": -1
    }

    def __init__(self, whitelist):
        self.access_whitelist_user = whitelist
        userdata_file = os.path.join(self.root_dir, "private", "userdata.json")
        if not os.path.exists(userdata_file):
            with open(userdata_file, 'w') as file:
                file.write("{}")
        with open(userdata_file, "r") as file:
            json_data = json.load(file)
            for key, value in json_data.items():
                self.data[key] = value

        for user in self.access_whitelist_user:
            if user not in self.data:
                self.data[user] = self.default_data

        self.save_userdata()

    def save_userdata(self):
        with open(os.path.join(self.root_dir, "private", "userdata.json"), "w") as file:
            json.dump(self.data, file)

    def add_new_user(self, username):
        self.data[username] = self.default_data
        self.save_userdata()

    def remove_user(self, username):
        self.data.pop(username, None)
        self.save_userdata()

    def get_usernames(self):
        return self.access_whitelist_user

    def set_wallet_payinvoice(self, username, invoice):
        self.data[username]["wallet"]["invoice"] = invoice
        self.save_userdata()

    def get_wallet_payinvoice(self, username):
        return self.data[username]["wallet"]["invoice"]

    def toggle_notifications_state(self, username, key):
        self.data[username]["wallet"]["notifications"][key] = not self.data[username]["wallet"]["notifications"][key]
        self.save_userdata()

    def get_notifications_state(self, username):
        return self.data[username]["wallet"]["notifications"]

    def set_chat_id(self, username, chat_id):
        self.data[username]["chat_id"] = chat_id
        self.save_userdata()

    def get_chat_id(self, username):
        return self.data[username]["chat_id"]

    def get_add_invoice_data(self, username):
        return self.data[username]["wallet"]["add_invoice_data"]

    def set_add_invoice_data(self, username, key, value):
        self.data[username]["wallet"]["add_invoice_data"][key] = value
        self.save_userdata()

    def delete_add_invoice_data(self, username):
        self.data[username]["wallet"]["add_invoice_data"] = {"amount": 0, "expiry": 3600, "description": ""}
        self.save_userdata()

    def set_conv_state(self, username, value):
        self.data[username]["conversation_state"] = value
        self.save_userdata()

    def get_conv_state(self, username):
        return self.data[username]["conversation_state"]

    def set_default_explorer(self, username, explorer_link):
        self.data[username]["wallet"]["default_explorer_tx"] = explorer_link
        self.save_userdata()

    def get_default_explorer(self, username):
        return self.data[username]["wallet"]["default_explorer_tx"]

    def set_pagination(self, username, page):
        self.data[username]["pagination_number"] = page
        self.save_userdata()

    def get_pagination(self, username):
        return self.data[username]["pagination_number"]

    def get_open_channel_data(self, username):
        return self.data[username]["wallet"]["open_channel_data"]

    def set_open_channel_data(self, username, key, value):
        self.data[username]["wallet"]["open_channel_data"][key] = value
        self.save_userdata()

    def delete_open_channel_data(self, username):
        self.data[username]["wallet"]["open_channel_data"] = {"address": "", "local_amount": 0, "target_conf": -1, "sat_per_byte": -1, "private": False, "min_htlc_msat": 1000, "remote_csv_delay": -1}
        self.save_userdata()

    def get_close_channel_data(self, username):
        return self.data[username]["wallet"]["close_channel_data"]

    def set_close_channel_data(self, username, key, value):
        self.data[username]["wallet"]["close_channel_data"][key] = value
        self.save_userdata()

    def delete_close_channel_data(self, username):
        self.data[username]["wallet"]["close_channel_data"] = {"chan_id": "", "target_conf": -1, "sat_per_byte": -1}
        self.save_userdata()

    def get_selected_unit(self, username):
        return self.data[username]["wallet"]["selected_unit"]

    def set_selected_unit(self, username, unit):
        self.data[username]["wallet"]["selected_unit"] = unit
        self.save_userdata()

    def get_onchain_send_data(self, username):
        return self.data[username]["wallet"]["onchain_send_data"]

    def set_onchain_send_data(self, username, key, value):
        self.data[username]["wallet"]["onchain_send_data"][key] = value
        self.save_userdata()

    def delete_onchain_send_data(self, username):
        self.data[username]["wallet"]["onchain_send_data"] = {"amount": 0, "address": "", "sat_per_byte": -1, "target_conf": -1}
        self.save_userdata()
