from pyzbar.pyzbar import decode
from PIL import Image
import os
from helper import logToFile
import subprocess
import json
from datetime import timedelta, datetime, timezone
import shlex
from node.local_node import LocalNode
from node.remote_node import RemoteNode


class Wallet:

    root_path = os.path.dirname(os.path.abspath(__file__))

    def __init__(self, userdata, node_conn="local", unit="sats"):
        self.userdata = userdata
        self.unit = unit  # units: sats, mBTC, BTC
        if node_conn == "local":
            self.node = LocalNode()  # TODO arguments
        else:
            self.node = RemoteNode("remote", pkey_name="id_rsa", ip="192.168.1.10")  # TODO arguments

    # TODO implement decodeInvoice, getNodeInfo for remote node
    def decodeInvoice(self, input_data, qr=True):
        try:
            if qr:
                ret = decode(Image.open(os.path.join(self.root_path, "temp", input_data)))
                if len(ret) > 0:
                    text = ret[0].data.decode("utf-8")
                else:
                    return "can't find and decode QR code", False
            else:
                text = input_data.strip()

            # remove uri "lightning:" from start of text
            if text[:10] == "lightning:":
                text = text[10:]

            decoded_data, error = self.node.decode_ln_invoice(pay_req=text)
            if error is None:
                info_data, error_info = self.node.get_ln_node_info(pub_key=decoded_data["destination"])  # get destination node info, alias...
                ret_data = {"decoded": decoded_data, "destination_node": info_data, "raw_invoice": text}
                return ret_data, True
            return "this is not valid invoice", False

        except Exception as e:
            logToFile("Exception at decoding invoice input data: "+str(e))
            return "there was error at decoding", False

    def formatDecodedInvoice(self, data, lb_symbol="\n"):

        d_time = datetime.fromtimestamp(int(data["decoded"]["timestamp"]), timezone.utc)
        expiry = int(data["decoded"]["expiry"])
        d_time_expiration = d_time + timedelta(seconds=expiry)

        to = data["destination_node"]["node"]["alias"] if data["destination_node"] else data["decoded"]["destination"]
        return "<b>Lightning invoice: </b>" + lb_symbol \
            + "To: " + to + lb_symbol \
            + "Amount: " + data["decoded"]["num_satoshis"] + " sats" + lb_symbol \
            + "Expiration: " + d_time_expiration.strftime('%d.%m.%Y %H:%M:%S %z') + lb_symbol \
            + "Description: " + data["decoded"]["description"] + lb_symbol

    def payInvoice(self, pay_req, bot, chat_id, username):
        try:
            out_json, error = self.node.pay_ln_invoice(pay_req)
            if error is None:
                if out_json["payment_error"] != "":
                    bot.send_message(chat_id=chat_id, text="I couldn't pay invoice, " + str(out_json["payment_error"]))
                    return
                else:
                    # as soon as we know payment was successful, clear invoice from user data
                    self.userdata[username]["wallet"]["invoice"] = None

                    total_amt = out_json["payment_route"]["total_amt"]
                    num_hops = len(out_json["payment_route"]["hops"])
                    if "total_fees_msat" in out_json["payment_route"]:
                        total_fees = out_json["payment_route"]["total_fees_msat"]
                    else:
                        total_fees = 0
                    msg_text = "<b>Invoice has been paid.</b>\n" \
                               + "Total amount: " + "{:,}".format(int(total_amt)).replace(',', '.') + " sats\n" \
                               + "Total fees: " + "{:,}".format(int(total_fees)).replace(',', '.') + " msats\n" \
                               + "hops: " + str(num_hops) + "\n"
                    import telegram
                    bot.send_message(chat_id=chat_id, text=msg_text, parse_mode=telegram.ParseMode.HTML)
                    return

            bot.send_message(chat_id=chat_id, text="I couldn't pay invoice, there was an error.")
        except Exception as e:
            text = str(e)
            logToFile("Exception payInvoice wallet: " + text)
            bot.send_message(chat_id=chat_id, text="I couldn't pay invoice, there was an error.")

    def addInvoice(self, memo="", value=0, expiry=3600):
        return self.node.add_ln_invoice(value, memo, expiry)

    def getInfo(self):
        return self.node.get_ln_info()

    def getOnchainAddress(self, type="p2wkh"):
        try:
            out_json, error = self.node.get_ln_onchain_address(addr_type=type)
            if error is None:
                return out_json["address"], None
            return None, error
        except Exception as e:
            text = str(e)
            logToFile("Exception getOnchainAddress: " + text)
            return None, text

    def getBalance(self):
        try:
            response, error = self.node.get_balance_report()
            if error is None:
                balance = {
                    "onchain_total": response["onchain"]["total_balance"],
                    "onchain_confirmed": response["onchain"]["confirmed_balance"],
                    "onchain_unconfirmed": response["onchain"]["unconfirmed_balance"],
                    "num_channels": len(response["ln"]["channels"]),
                    "num_active": 0,
                    "channels": {
                        "effective_outbound_capacity": 0,
                        "effective_inbound_capacity": 0,
                        "effective_max_outbound_payment": 0,
                        "effective_max_inbound_payment": 0,
                        "outbound_capacity": 0,
                        "inbound_capacity": 0,
                        "max_outbound_payment": 0,
                        "max_inbound_payment": 0,
                        "inactive_aliases": []
                    }
                }
                num_active = 0
                for channel in response["ln"]["channels"]:
                    balance["channels"]["outbound_capacity"] += int(channel["local_balance"])
                    balance["channels"]["inbound_capacity"] += int(channel["remote_balance"])

                    if int(channel["local_balance"]) > balance["channels"]["max_outbound_payment"]:
                        balance["channels"]["max_outbound_payment"] = int(channel["local_balance"])
                    if int(channel["remote_balance"]) > balance["channels"]["max_inbound_payment"]:
                        balance["channels"]["max_inbound_payment"] = int(channel["remote_balance"])

                    if channel["active"]:
                        num_active += 1
                        balance["channels"]["effective_outbound_capacity"] += int(channel["local_balance"])
                        balance["channels"]["effective_inbound_capacity"] += int(channel["remote_balance"])

                        if int(channel["local_balance"]) > balance["channels"]["effective_max_outbound_payment"]:
                            balance["channels"]["effective_max_outbound_payment"] = int(channel["local_balance"])
                        if int(channel["remote_balance"]) > balance["channels"]["effective_max_inbound_payment"]:
                            balance["channels"]["effective_max_inbound_payment"] = int(channel["remote_balance"])
                    else:
                        node_alias = self.getNodeInfo(channel["remote_pubkey"])
                        if node_alias is None:
                            balance["channels"]["inactive_aliases"].append(channel["remote_pubkey"][:12])
                        else:
                            balance["channels"]["inactive_aliases"].append(node_alias["node"]["alias"])

                balance["num_active"] = num_active
                return balance, None
            return None, error
        except Exception as e:
            text = str(e)
            logToFile("Exception at getBalance: "+text)
            return None, text

    def formatBalanceOutput(self, data, lb_symbol="\n"):
        text = "<b>Balance report</b>" + lb_symbol \
            + "<i>🔗 On-chain:</i>" + lb_symbol \
            + "Total: {0}" + lb_symbol + "Confirmed: {1}" + lb_symbol + "Unconfirmed: {2}" + lb_symbol + lb_symbol \
            + "<i>⚡ Lightning channels: ("+str(data["num_active"])+"/"+str(data["num_channels"])+")</i>" + lb_symbol \
            + "<i>--Capacities</i>" + lb_symbol \
            + "Effective outbound: {3}" + lb_symbol \
            + "Effective inbound: {4}" + lb_symbol \
            + "Outbound: {7}" + lb_symbol \
            + "Inbound: {8}" + lb_symbol \
            + "<i>--Max possible single payment size</i>" + lb_symbol \
            + "Effective outbound: {5}" + lb_symbol \
            + "Effective inbound: {6}" + lb_symbol \
            + "Outbound: {9}" + lb_symbol \
            + "Inbound: {10}" + lb_symbol \
            + "<i>--Inactive channels</i>" + lb_symbol \
            + ", ".join(data["channels"]["inactive_aliases"])

        args = [
            data["onchain_total"], data["onchain_confirmed"], data["onchain_unconfirmed"],
            data["channels"]["effective_outbound_capacity"], data["channels"]["effective_inbound_capacity"],
            data["channels"]["effective_max_outbound_payment"], data["channels"]["effective_max_inbound_payment"],
            data["channels"]["outbound_capacity"], data["channels"]["inbound_capacity"],
            data["channels"]["max_outbound_payment"], data["channels"]["max_inbound_payment"]
        ]

        if self.unit == "BTC":
            for idx, arg in enumerate(args):
                args[idx] = str("%.8f" % arg/100000000.0)
        elif self.unit == "mBTC":
            for idx, arg in enumerate(args):
                args[idx] = str("%.5f" % arg / 100000.0)

        for idx, arg in enumerate(args):
            args[idx] = "{:,}".format(int(arg)).replace(',', '.') + " " + self.unit
        text = text.format(*args)
        return text
