import asyncio, server

async def main():
    print("1) Upload artwork tu file local...")
    img = await server.upload_artwork_file("Sunfade_Yosemite_RetroSunburst_7200x10800.png", "Yosemite_test.png")
    print("   image_id:", img)

    print("\n2) Tao san pham tu template...")
    res = await server.create_from_template(
        template_name="poster_matte_2x3",
        image_id=img["id"],
    )
    print("   ", res)

    print("\n3) Verify...")
    print("   ", await server.get_product(res["product_id"]))
    print("\nDONE - vao Printify > My Products xem san pham draft.")

asyncio.run(main())