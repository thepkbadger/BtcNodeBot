import os
import json


class UserData:

    root_dir = os.path.dirname(os.path.abspath(__file__))
    data = {}
    default_data = {
        "wallet": {"invoice": None, "node_watch_mute": False, "add_invoice_data": {"amount": 0, "expiry": 3600, "description": ""}},
        "chat_id": None,
        "conversation_state": None
    }

    def __init__(self, whitelist):
        self.access_whitelist_user = whitelist
        with open(os.path.join(self.root_dir, "private", "userdata.json"), "r") as file:
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

    def set_node_watch_mute(self, username, value):
        self.data[username]["wallet"]["node_watch_mute"] = value
        self.save_userdata()

    def get_node_watch_mute(self, username):
        return self.data[username]["wallet"]["node_watch_mute"]

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
