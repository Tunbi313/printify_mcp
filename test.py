import asyncio, server

async def main():
    print("Shops:", await server.list_shops())
    print("Templates:", server.list_templates())
    print("\nOK - token hoat dong, ket noi Printify thanh cong.")

asyncio.run(main())