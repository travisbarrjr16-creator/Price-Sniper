# Run Price Sniper on Google Cloud's free server

For when you don't have a computer that stays on. Google Cloud's **Always Free** tier includes one small server (an "e2-micro") that can run Price Sniper 24/7 for you and up to 5 friends, connected privately through Tailscale.

> **Read this first: it's *probably* $0, but watch your first bill.**
> - Since Feb 2024 Google charges for public IP addresses (about **$3.60/month**). Some users say the free tier covers one IP on the free server, but others report being billed ([Google's notice](https://cloud.google.com/vpc/pricing-announce-external-ips), [discussion](https://lowendspirit.com/discussion/comment/26480)). The server needs one to reach eBay, Reddit, and the other sites.
> - Google requires a credit card. Set a **$1 budget alert** (step 6) so you hear about any charge right away.
> - Free tier terms come from Google and can change. Check [cloud.google.com/free](https://cloud.google.com/free) before you start.

Google renames buttons now and then, so if a label below doesn't match exactly, look for the closest one.

---

## 1. Make the account
1. Go to [console.cloud.google.com](https://console.cloud.google.com) and sign in. Use your deals-only Gmail if you like.
2. Accept the free trial and add a card. The $300 trial credit is separate from Always Free, and Always Free keeps going after the trial ends.

## 2. Create the server (Compute Engine → VM instances → Create instance)
Get **every** one of these right. Each one decides whether it's free:

| Setting | Choose | Why |
|---|---|---|
| **Region** | `us-central1` (Iowa), `us-west1` (Oregon), or `us-east1` (South Carolina) | The free server only exists in these three |
| **Machine type** | **e2-micro** | The only free size |
| **Provisioning / VM model** | **Standard** (not Spot/preemptible) | Spot VMs get shut down at random |
| **Boot disk → type** | **Standard persistent disk** | ⚠ The default is usually *Balanced*, **which is billed** |
| **Boot disk → size** | **30 GB** or less | Over 30 GB, the whole disk is billed |
| **Boot disk → image** | Debian 12 or Ubuntu 24.04 LTS | Both work with the setup script |
| **Firewall** | Leave HTTP/HTTPS **unticked** | Tailscale doesn't need any open ports |

Click **Create**. Only ever run **one** e2-micro, because the free hours cover one server for the whole month.

## 3. Connect to it
In the VM list, click **SSH** next to your server. A terminal opens in your browser. Everything below is typed there.

## 4. Install the basics and the app
```
sudo apt update && sudo apt install -y git python3 python3-venv
# 1 GB of memory is tight: add 2 GB of swap so installs don't get killed
sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile && sudo mkswap /swapfile && sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
git clone https://github.com/travisbarrjr16-creator/Price-Sniper.git
cd Price-Sniper
```

## 5. Tailscale, then the setup script
```
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up            # opens a sign-in link: open it on your phone/PC and approve
sudo tailscale set --operator=$USER    # lets the setup script run "tailscale serve" without sudo
scripts/home/setup.sh
sudo loginctl enable-linger $USER      # keep the app running after you close the SSH window
```
Before running the setup script, enable HTTPS in the Tailscale [admin console](https://login.tailscale.com/admin/dns) (DNS → **Enable HTTPS**). When the script finishes, it prints your `https://…ts.net` address and the invite code. From here on, **HOME_SETUP.md steps 6–10** apply exactly the same: make your account, add friends, add site keys, updating, backups.

## 6. Set a budget alert (do this today)
Console → **Billing** → **Budgets & alerts** → **Create budget** → amount **$1** → alerts at 50%, 90%, and 100%. You'll get an email if anything starts costing money. Check **Billing → Reports** after the first few days too.

## 7. Backups off the server
The app backs itself up daily into `~/Price-Sniper/backups/`, but that's on the same server. Now and then, download the newest file. In the SSH window, use the ⚙ menu → **Download file**, and enter `Price-Sniper/backups/` plus the newest file name.

---

### If something goes wrong
- **Install killed / "Killed" message:** the swap step was skipped. Run the swap commands in step 4, then `scripts/home/setup.sh` again.
- **App not reachable:** `systemctl --user status price-sniper` and `tailscale serve status`.
- **Unexpected charge:** check the disk type is *Standard*, the region is one of the three, there's only one VM, and no extra disks or static IPs exist. If the only charge is the external IP (~$3.60/mo), that's the IPv4 fee from the note at the top.
