import asyncio
from pymodbus.client import AsyncModbusSerialClient

async def main():
    client = AsyncModbusSerialClient(
        port="/dev/ttyUSB0",
        baudrate=9600,
        parity="N",
        stopbits=1,
        bytesize=8,
        timeout=1
    )

    await client.connect()

    result = await client.read_holding_registers(
        address=0,
        count=2,
        device_id=2   # ← ini yang benar
    )

    if result.isError():
        print("Gagal baca")
    else:
        print("Register:", result.registers)

    await client.close()

asyncio.run(main())