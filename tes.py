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

    # baca slave 2 register 0 sebanyak 2
    result = await client.read_holding_registers(
        address=0,
        count=2,
        slave=2
    )

    if result.isError():
        print("Gagal baca")
    else:
        print("Data:", result.registers)

    await client.close()

asyncio.run(main())