import os
import uuid
from helper import logToFile
from multiprocessing import Process
import matplotlib.pyplot as plt
from matplotlib import style
import matplotlib.ticker as mticker
import matplotlib as mpl
mpl.use('Agg')


class Plot:

    root_path = os.path.dirname(os.path.abspath(__file__))

    def __init__(self, ln_channels, node_info=None):
        self.channels = ln_channels
        self.node_info = node_info

    def get_capacity_pct(self):
        for idx, ch in enumerate(self.channels):
            total = int(ch["local_balance"]) + int(ch["remote_balance"])
            self.channels[idx]["local_balance_pct"] = int(round((int(ch["local_balance"]) / total) * 100))
            self.channels[idx]["remote_balance_pct"] = int(round((int(ch["remote_balance"]) / total) * 100))

    def plot_cap_dist(self, plot_type):
        try:
            image_name = str(uuid.uuid4().hex) + ".png"
            image_path = os.path.join(self.root_path, "temp", image_name)
            p = Process(name="btcnodebot-plot_cap_dist", target=self.create_plot_cap_dist, args=[image_path, plot_type])
            p.start()
            p.join()
            return image_name
        except Exception as e:
            logToFile("Exception plot_cap_dist: " + str(e))
            return ""

    def create_plot_cap_dist(self, image_path, plot_type):
        ax = None
        fig = None
        try:
            if plot_type not in ["cdbar", "cdscatter"]:
                plot_type = "cdbar"

            self.get_capacity_pct()
            hist_local = [0] * 101
            for ch in self.channels:
                hist_local[ch["local_balance_pct"]] += 1

            fig = plt.figure(figsize=(19.2, 10.8))
            ax = plt.gca()
            style.use('seaborn-whitegrid')
            node_data = ""
            if self.node_info:
                pubkey_short = self.node_info["identity_pubkey"][:8] + "..." + self.node_info["identity_pubkey"][-8:]
                node_data = "\n" + self.node_info["alias"] + " (" + pubkey_short + ")"

            fig.suptitle("Capacity Distribution" + node_data, fontsize=19, fontweight='bold')

            major_ticks = [i for i in range(101) if i % 5 == 0]
            minor_ticks = [i for i in range(101) if i % 5 != 0]
            ax.set_xticks(major_ticks)
            ax.set_xticks(minor_ticks, minor=True)
            ax.tick_params('both', labelsize=14)
            ax.set_xlim(left=-1, right=101)

            if plot_type == "cdbar":
                ax.set_xlabel("Local Balance (%)\n<= more funds on remote side | more funds on local side =>", fontsize=17)
                ax.set_ylabel("Number of channels", fontsize=17)
                ax.yaxis.set_major_locator(mticker.MaxNLocator(integer=True, min_n_ticks=1))
                y_top = max(hist_local) if max(hist_local) > 0 else 1
                ax.set_ylim(bottom=0, top=y_top)
                ax.grid(b=True, axis='y', which='major')

                ax.bar([i for i in range(0, 101)], hist_local, align='center', alpha=0.75, color="#2196f3")
                ax.axvspan(40, 60, alpha=0.2, color='gray')
                ax.axvspan(30, 70, alpha=0.1, color='gray')
                plt.text(x=50, y=y_top / 2, s="Optimal Range", fontdict={'ha': 'center', 'va': 'center', 'alpha': 0.5, 'fontsize': 17})

            elif plot_type == "cdscatter":
                cap_local = []
                cap_remote = []
                count = []
                for local_pct, cnt in enumerate(hist_local):
                    if cnt > 0:
                        cap_local.append(local_pct)
                        cap_remote.append(100 - local_pct)
                        count.append(cnt)

                ax.set_xlabel("Local Balance (%)", fontsize=17)
                ax.set_yticks(major_ticks)
                ax.set_yticks(minor_ticks, minor=True)
                ax.set_ylabel("Remote Balance (%)", fontsize=17)
                ax.set_ylim(bottom=-1, top=101)
                ax.grid(b=True, which='major', alpha=0.5)
                sc = ax.scatter(x=cap_local, y=cap_remote, s=100, c=count, cmap="gist_rainbow")
                cbar = plt.colorbar(sc, fraction=0.03, ticks=mticker.MaxNLocator(integer=True, min_n_ticks=1))
                cbar.set_label("Number of channels", fontsize=17)
                cbar.ax.tick_params(labelsize=14)

                x = 30
                y = 65
                while x < 70:
                    if x < 40 or x > 55:
                        alpha = 0.1
                    else:
                        alpha = 0.3
                    rect = plt.Rectangle((x, y), 5, 5, color='gray', alpha=alpha)
                    ax.add_artist(rect)
                    x += 5
                    y -= 5

                plt.text(x=56, y=52.5, s="Optimal Area", fontdict={'ha': 'center', 'va': 'center', 'alpha': 0.5, 'fontsize': 17})
                plt.text(x=77.5, y=10, s="funds mostly on local side", fontdict={'ha': 'center', 'va': 'center', 'alpha': 0.5, 'fontsize': 17})
                plt.text(x=23.5, y=90, s="funds mostly on remote side", fontdict={'ha': 'center', 'va': 'center', 'alpha': 0.5, 'fontsize': 17})

            plt.savefig(image_path)
            plt.close(fig)
            mpl.rcParams.update(mpl.rcParamsDefault)  # set default style back
        except Exception as e:
            logToFile("Exception create_plot_cap_dist: " + str(e))
        finally:
            del ax, fig
