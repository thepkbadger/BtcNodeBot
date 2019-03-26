import os
from os.path import expanduser
from helper import logToFile
import socket
import subprocess
import json
from time import sleep
import telegram


class RemoteNode:

    root_dir = os.path.dirname(os.path.abspath(__file__))

    payload = {
        "btc_getblockchaininfo": {"service": "bitcoin-cli", "command": "getblockchaininfo"},
        "btc_getnetworkinfo": {"service": "bitcoin-cli", "command": "getnetworkinfo"},
        "ln_getinfo": {"service": "ln", "command": "getinfo"},
        "ln_getnewaddress": {"service": "ln", "command": "newaddress", "arguments": {"addresstype": ""}},
        "ln_addinvoice": {"service": "ln", "command": "addinvoice", "arguments": {"expiry": "", "value": "", "memo": ""}},
        "ln_sendpayment": {"service": "ln", "command": "sendpayment", "arguments": {"pay_req": ""}},
        "balance_report": {"service": "balance_report", "command": ""},
        "ln_sub_invoices": {"service": "subscribe", "command": "invoices"}
    }

    def __init__(self, username, pkey_name="", ip="", ddns_hostname="", pkey_path="", ssh_port=22):
        self.ssh_port = ssh_port
        self.remote_ip = ip
        self.ddns_hostname = ddns_hostname
        self.user = username

        if pkey_path != "":
            self.ssh_pkey_file = pkey_path
        else:
            self.ssh_pkey_file = os.path.join(expanduser("~"), ".ssh", pkey_name)

        self.onlineBitcoin = False
        self.onlineLightning = False
        self.check_node_online()

    def get_ip(self):
        try:
            if self.remote_ip == "":
                self.remote_ip = socket.gethostbyname(self.ddns_hostname)
            return True
        except Exception as e:
            logToFile("Exception RemoteNode get_connarg: " + str(e))
            return False

    def check_node_online(self, backend_tolerate_sync_thr=1, ln_tolerate_sync_thr=1):
        try:
            self.get_ip()
            conn_string = "ssh "+self.user+"@"+self.remote_ip+" -i "+self.ssh_pkey_file+" "

            proc = subprocess.Popen(conn_string+json.dumps(json.dumps(self.payload["btc_getblockchaininfo"])), shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            response = proc.communicate()
            err_blockchaininfo = response[1].decode("utf-8")
            if err_blockchaininfo == "":
                blockchaininfo = json.loads(response[0].decode("utf-8"))
            if proc.returncode is None:
                proc.kill()

            proc = subprocess.Popen(conn_string+json.dumps(json.dumps(self.payload["btc_getnetworkinfo"])), shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            response = proc.communicate()
            err_networkinfo = response[1].decode("utf-8")
            if err_networkinfo == "":
                networkinfo = json.loads(response[0].decode("utf-8"))
            if proc.returncode is None:
                proc.kill()

            proc = subprocess.Popen(conn_string+json.dumps(json.dumps(self.payload["ln_getinfo"])), shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            response = proc.communicate()
            err_ln_getinfo = response[1].decode("utf-8")
            if err_ln_getinfo == "":
                lninfo = json.loads(response[0].decode("utf-8"))
            if proc.returncode is None:
                proc.kill()

            if err_blockchaininfo != "" or err_networkinfo != "":
                err_networkinfo = err_networkinfo.replace('\n', '')
                err_networkinfo = err_networkinfo.replace('\r', '')
                err_blockchaininfo = err_blockchaininfo.replace('\n', '')
                err_blockchaininfo = err_blockchaininfo.replace('\r', '')
                self.onlineBitcoin = False
                self.onlineLightning = False
                return {"bitcoind": {"online": False, "msg": "stderrs: ["+err_blockchaininfo+"] ["+err_networkinfo+"]"}, "ln": {"online": False}}
            elif err_ln_getinfo != "":
                self.onlineBitcoin = True
                self.onlineLightning = False
                return {
                    "bitcoind": {
                        "online": True,
                        "subversion": networkinfo["subversion"],
                        "protocolversion": networkinfo["protocolversion"],
                        "blocks": blockchaininfo["blocks"],
                        "headers": blockchaininfo["headers"],
                        "synced": True if (blockchaininfo["headers"] - blockchaininfo["blocks"]) <= backend_tolerate_sync_thr else False
                    },
                    "ln": {"online": False, "msg": "stderrs: ["+err_ln_getinfo+"]"}
                }
            else:
                self.onlineBitcoin = True
                self.onlineLightning = True
                return {
                    "bitcoind": {
                        "online": True,
                        "subversion": networkinfo["subversion"],
                        "protocolversion": networkinfo["protocolversion"],
                        "blocks": blockchaininfo["blocks"],
                        "headers": blockchaininfo["headers"],
                        "synced": True if (blockchaininfo["headers"] - blockchaininfo["blocks"]) <= backend_tolerate_sync_thr else False
                    },
                    "ln": {
                        "online": True,
                        "block_height": lninfo["block_height"],
                        "synced": True if (blockchaininfo["headers"] - lninfo["block_height"]) <= ln_tolerate_sync_thr else False,
                        "version": lninfo["version"]
                    }
                }

        except Exception as e:
            text = str(e)
            logToFile("Exception check_node_online: " + text)
            self.onlineBitcoin = False
            self.onlineLightning = False
            return {"online": None, "msg": "Exception: " + text}

    def get_ln_info(self):
        try:
            self.get_ip()
            conn_string = "ssh " + self.user + "@" + self.remote_ip + " -i " + self.ssh_pkey_file + " "
            proc = subprocess.Popen(conn_string + json.dumps(json.dumps(self.payload["ln_getinfo"])), shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            response = proc.communicate()
            if proc.returncode is None:
                proc.kill()

            err = response[1].decode("utf-8")
            if err == "":
                return json.loads(response[0].decode("utf-8")), None
            return None, err

        except Exception as e:
            text = str(e)
            logToFile("Exception get_ln_node_info: " + text)
            return None, text

    def get_ln_onchain_address(self, addr_type):
        try:
            self.get_ip()
            conn_string = "ssh " + self.user + "@" + self.remote_ip + " -i " + self.ssh_pkey_file + " "
            command = self.payload["ln_getnewaddress"]
            command["arguments"]["addresstype"] = str(addr_type)

            proc = subprocess.Popen(conn_string + json.dumps(json.dumps(command)), shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            response = proc.communicate()
            if proc.returncode is None:
                proc.kill()

            err = response[1].decode("utf-8")
            if err == "":
                return json.loads(response[0].decode("utf-8")), None
            return None, err

        except Exception as e:
            text = str(e)
            logToFile("Exception get_ln_onchain_address: " + text)
            return None, text

    def pay_ln_invoice(self, pay_req):
        try:
            self.get_ip()
            conn_string = "ssh " + self.user + "@" + self.remote_ip + " -i " + self.ssh_pkey_file + " "
            command = self.payload["ln_sendpayment"]
            command["arguments"]["pay_req"] = pay_req

            proc = subprocess.Popen(conn_string + json.dumps(json.dumps(command)), shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            response = proc.communicate()
            if proc.returncode is None:
                proc.kill()

            err = response[1].decode("utf-8")
            if err == "":
                return json.loads(response[0].decode("utf-8")), None
            return None, err

        except Exception as e:
            text = str(e)
            logToFile("Exception pay_ln_invoice: " + text)
            return None, text

    def add_ln_invoice(self, value_sats, memo, expiry_sec):
        try:
            self.get_ip()
            conn_string = "ssh " + self.user + "@" + self.remote_ip + " -i " + self.ssh_pkey_file + " "
            command = self.payload["ln_addinvoice"]
            command["arguments"]["memo"] = memo
            command["arguments"]["value"] = int(value_sats)
            command["arguments"]["expiry"] = int(expiry_sec)

            proc = subprocess.Popen(conn_string + json.dumps(json.dumps(command)), shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            response = proc.communicate()
            if proc.returncode is None:
                proc.kill()

            err = response[1].decode("utf-8")
            if err == "":
                return json.loads(response[0].decode("utf-8")), None
            return None, err

        except Exception as e:
            text = str(e)
            logToFile("Exception add_ln_invoice: " + text)
            return None, text

    def get_balance_report(self):
        try:
            self.get_ip()
            conn_string = "ssh " + self.user + "@" + self.remote_ip + " -i " + self.ssh_pkey_file + " "

            proc = subprocess.Popen(conn_string + json.dumps(json.dumps(self.payload["balance_report"])), shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            response = proc.communicate()
            if proc.returncode is None:
                proc.kill()

            err = response[1].decode("utf-8")
            if err == "":
                return json.loads(response[0].decode("utf-8")), None
            return None, err

        except Exception as e:
            text = str(e)
            logToFile("Exception get_balance_report: " + text)
            return None, text

    def subscribe_invoices(self, bot, chat_id):
        try:
            sleep_retry = 65
            sleep_offline = 20

            while True:
                if not self.onlineBitcoin or not self.onlineLightning:
                    sleep(sleep_offline)  # if we know node is offline, sleep and retry
                    continue
                self.get_ip()
                # use tty to close child processes when connection is killed unexpectedly
                # Multiple -t options force tty allocation, even if ssh has no local tty
                conn_string = "ssh -t -t " + self.user + "@" + self.remote_ip + " -i " + self.ssh_pkey_file + " "
                proc = subprocess.Popen(conn_string + json.dumps(json.dumps(self.payload["ln_sub_invoices"])), shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

                while True:
                    output = proc.stdout.readline().decode("utf-8")
                    if output == "" and proc.poll() is not None:
                        break
                    if output:
                        output.strip()
                        json_out = json.loads(output)["result"]

                        # received payment
                        if "settled" in json_out and json_out["settled"] is True:
                            text = "<b>Received LN payment.</b>\n"
                            if "amt_paid_sat" in json_out:
                                text += "Amount: "+"{:,}".format(int(json_out["amt_paid_sat"])).replace(',', '.')+" sats\n"
                            if "memo" in json_out:
                                text += "Description: "+json_out["memo"]
                            bot.send_message(chat_id=chat_id, text=text, parse_mode=telegram.ParseMode.HTML)

                proc.stdout.close()
                proc.stderr.close()
                proc.kill()
                print("LiveFeed RemoteNode subscribe invoices: connection lost, will retry after " + str(sleep_retry) + " seconds")
                sleep(sleep_retry)
        except Exception as e:
            text = "LiveFeed RemoteNode subscribe invoices: stopped, Exception=" + str(e)
            logToFile(text)
            print(text)
