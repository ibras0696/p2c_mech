import paramiko, warnings, sys
warnings.filterwarnings("ignore")

# usage:
#   python3 logs.py            -> follow live, only claim results (Ctrl+C to stop)
#   python3 logs.py all        -> follow live, everything
#   python3 logs.py 200        -> last 200 claim results then follow
#   python3 logs.py 200 once   -> last 200 claim results and exit
#   python3 logs.py all 200    -> last 200 of everything then follow

args = sys.argv[1:]
show_all = "all" in args
args = [a for a in args if a != "all"]
ws_mode = "ws" in args
args = [a for a in args if a != "ws"]
once = "once" in args
args = [a for a in args if a != "once"]
n = args[0] if args else "200"

if ws_mode:
    # raw socket events: all event names, batch counts, payload snippets
    FILTER = "packet_received\\|events_received\\|claim_started\\|claim_failed\\|claim_succeeded"
else:
    # claim outcome lines only
    FILTER = "claim_failed\\|claim_succeeded\\|claim_won\\|take_result\\|claim_skipped"

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect('147.45.170.197', username='root', password='r-6M1L^B?RXHSd', timeout=30)

flag = "" if once else "-f"
base = f'cd /opt/settings_dep_cloude && docker compose logs {flag} --tail={n} bot 2>&1'
cmd = base if show_all else f'{base} | grep --line-buffered "{FILTER}"'

chan = c.get_transport().open_session()
chan.get_pty()
chan.exec_command(cmd)

try:
    while True:
        if chan.recv_ready():
            sys.stdout.write(chan.recv(4096).decode(errors="replace"))
            sys.stdout.flush()
        elif chan.exit_status_ready():
            break
except KeyboardInterrupt:
    print("\n[stopped]")
finally:
    chan.close()
    c.close()
