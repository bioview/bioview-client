import socket 

def get_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(0)
    try:
        # Doesn't even have to be reachable
        s.connect(('8.8.8.8', 80)) # Google DNS servers
        IP = s.getsockname()[0]
    except Exception:
        IP = '127.0.0.1' # localhost
    finally:
        s.close()
    
    return IP