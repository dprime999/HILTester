# Setting Up Tailscale on a Raspberry Pi 5 (with VS Code SSH Access)

## 1. Install Tailscale on the Pi 5
SSH into your Pi (or use a terminal on it) and run:
```
curl -fsSL https://tailscale.com/install.sh | sh
```

## 2. Connect the Pi to your Tailscale account
Run:
```
sudo tailscale up
```
This prints a login URL — open it in a browser and sign in with the account you're using for Tailscale (e.g. Google). This authorizes the Pi as a device on your tailnet.

**Set the key to never expire:** in the Tailscale admin console, go to **Machines**, find the Pi, click the **⋯** menu → **Disable key expiry**. Otherwise the Pi's connection will expire (default 180 days) and it'll drop off your tailnet until you run `sudo tailscale up` again.

## 3. Connect your host PC too
Install Tailscale on your host machine (Windows/Mac/Linux) and log in with the **same account**. Both devices need to be logged in and connected before they can see each other.

## 4. Verify the connection
On either machine, run:
```
tailscale status
```
Confirm both devices show up and are online. Note the Pi's Tailscale IP (looks like `100.x.x.x`) or its MagicDNS name (e.g. `raspberrypi.tailnet-name.ts.net`).

## 5. Add the Pi as an SSH host in VS Code
Open the Remote-SSH extension config:
- `Cmd/Ctrl+Shift+P` → **Remote-SSH: Open SSH Configuration File**

Add an entry:
```
Host pi5
    HostName <tailscale-ip-or-magicdns-name>
    User <your-pi-username>
```
Save it, then connect via **Remote-SSH: Connect to Host**.

## 6. Reconnect if a device drops off
If either machine gets disconnected from the tailnet (reboot, logout, re-install, etc.), you'll likely need to redo `sudo tailscale up` on that device and log in again before the connection works. Just repeat step 2 or 3 as needed.

---

### Notes
- You'll need the **VS Code Remote-SSH extension** installed if you don't have it already.
- The `User` in your SSH config is your Pi login username (usually `pi` unless you changed it).
- MagicDNS names are more stable than IPs for the VS Code config, since Tailscale IPs generally stay fixed per-device but the DNS name is easier to remember.
