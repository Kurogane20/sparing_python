from pymodbus.client import ModbusSerialClient

client = ModbusSerialClient(
    port="/dev/ttyUSB0",
    baudrate=9600,
    parity="N",
    stopbits=1,
    bytesize=8,
    timeout=1
)

client.connect()

# contoh baca slave 1 register 0 sebanyak 2
result = client.read_holding_registers(1, 0, 2)

if result.isError():
    print("Gagal baca")
else:
    print("Data:", result.registers)

client.close()