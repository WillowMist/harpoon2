from lib.rtorrent.lib.xmlrpc.clients.scgi import SCGIServerProxy

client = SCGIServerProxy("scgi://127.0.0.1:5002/")
result = client.system.api_version()
print("API Version:", result)
