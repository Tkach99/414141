
import asyncio
import threading
from kivy.app import App
from kivy.clock import Clock
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.label import Label
from kivy.uix.scrollview import ScrollView
from kivy.uix.textinput import TextInput

# Android BLE is accessed through pyjnius. This first build intentionally
# discovers GATT services/characteristics instead of hard-coding one UUID,
# because G2 Master revisions can expose different BLE profiles.

try:
    from jnius import autoclass, PythonJavaClass, java_method
    ANDROID = True
except Exception:
    ANDROID = False

class GattCallback(PythonJavaClass):
    __javainterfaces__ = ['android/bluetooth/BluetoothGattCallback']

    def __init__(self, owner):
        super().__init__()
        self.owner = owner

    @java_method('(Landroid/bluetooth/BluetoothGatt;I)V')
    def onConnectionStateChange(self, gatt, status, newState):
        Clock.schedule_once(lambda dt: self.owner.log(
            f"connection status={status}, state={newState}"))
        # STATE_CONNECTED = 2
        if newState == 2:
            gatt.discoverServices()

    @java_method('(Landroid/bluetooth/BluetoothGatt;I)V')
    def onServicesDiscovered(self, gatt, status):
        Clock.schedule_once(lambda dt: self.owner.services_found(gatt, status))

    @java_method('(Landroid/bluetooth/BluetoothGatt;Landroid/bluetooth/BluetoothGattCharacteristic;I)V')
    def onCharacteristicWrite(self, gatt, characteristic, status):
        Clock.schedule_once(lambda dt: self.owner.log(
            f"WRITE {characteristic.getUuid()} status={status}"))

    @java_method('(Landroid/bluetooth/BluetoothGatt;Landroid/bluetooth/BluetoothGattCharacteristic;[B)V')
    def onCharacteristicChanged(self, gatt, characteristic, value):
        data = bytes(value)
        Clock.schedule_once(lambda dt: self.owner.rx(characteristic.getUuid(), data))


class Main(App):
    def build(self):
        self.gatt = None
        self.write_char = None
        self.notify_char = None
        self.callback = GattCallback(self)

        box = BoxLayout(orientation="vertical", padding=8, spacing=6)
        self.status = Label(text="KuKirin G2 Master BLE\nNot connected", size_hint_y=None, height=70)
        box.add_widget(self.status)

        row = BoxLayout(size_hint_y=None, height=50, spacing=5)
        for title, fn in [
            ("SCAN", self.scan),
            ("DISCONNECT", self.disconnect),
            ("SERVICES", self.show_services),
        ]:
            b = Button(text=title)
            b.bind(on_press=lambda btn, f=fn: f())
            row.add_widget(b)
        box.add_widget(row)

        row2 = BoxLayout(size_hint_y=None, height=55, spacing=5)
        for title, payload in [
            ("WALK", bytes.fromhex("F0 4C 03 00")),
            ("FAST", bytes.fromhex("F0 4C 30 02")),
            ("SLOW", bytes.fromhex("F0 4C 30 01")),
        ]:
            b = Button(text=title)
            b.bind(on_press=lambda btn, p=payload: self.send(p))
            row2.add_widget(b)
        box.add_widget(row2)

        self.manual = TextInput(
            text="",
            hint_text="HEX command, e.g. F0 4C 03 00",
            multiline=False,
            size_hint_y=None,
            height=50,
        )
        box.add_widget(self.manual)

        b = Button(text="SEND HEX", size_hint_y=None, height=50)
        b.bind(on_press=lambda *_: self.send_hex())
        box.add_widget(b)

        self.log_view = TextInput(readonly=True, multiline=True)
        box.add_widget(self.log_view)
        return box

    def log(self, s):
        self.log_view.text += str(s) + "\n"

    def rx(self, uuid, data):
        self.log(f"RX {uuid}: {data.hex(' ').upper()}")

    def services_found(self, gatt, status):
        self.log(f"services discovered status={status}")
        self.write_char = None
        self.notify_char = None
        services = gatt.getServices()
        for i in range(services.size()):
            svc = services.get(i)
            su = str(svc.getUuid())
            self.log(f"SERVICE {su}")
            chars = svc.getCharacteristics()
            for j in range(chars.size()):
                c = chars.get(j)
                props = c.getProperties()
                cu = str(c.getUuid())
                self.log(f"  CHAR {cu} props=0x{props:02X}")
                # Android BluetoothGattCharacteristic.PROPERTY_WRITE = 0x08
                # PROPERTY_WRITE_NO_RESPONSE = 0x04
                # PROPERTY_NOTIFY = 0x10
                if self.write_char is None and (props & 0x08 or props & 0x04):
                    self.write_char = c
                if self.notify_char is None and (props & 0x10):
                    self.notify_char = c
        self.status.text = "CONNECTED / GATT READY"
        if self.notify_char is not None:
            try:
                gatt.setCharacteristicNotification(self.notify_char, True)
                self.log(f"NOTIFY enabled on {self.notify_char.getUuid()}")
            except Exception as e:
                self.log(f"notify enable failed: {e}")
        self.log("WRITE characteristic: " + (
            str(self.write_char.getUuid()) if self.write_char else "NONE"))

    def scan(self):
        if not ANDROID:
            self.log("Run this on Android; pyjnius is unavailable on desktop.")
            return
        try:
            BluetoothAdapter = autoclass("android.bluetooth.BluetoothAdapter")
            self.adapter = BluetoothAdapter.getDefaultAdapter()
            if self.adapter is None or not self.adapter.isEnabled():
                self.log("Bluetooth is off/unavailable.")
                return
            self.log("Paired devices:")
            devices = self.adapter.getBondedDevices()
            for i in range(devices.size()):
                d = devices.iterator().next() if False else devices.toArray()[i]
                self.log(f"{d.getName()}  {d.getAddress()}")
            self.log("Use Android system Bluetooth pairing if G2 is not paired.")
            self.status.text = "Bluetooth ready — pair the scooter, then tap SCAN again."
        except Exception as e:
            self.log(f"scan error: {e}")

    def connect_address(self, address):
        if not ANDROID:
            return
        try:
            device = self.adapter.getRemoteDevice(address)
            self.log(f"Connecting {device.getName()} {address}")
            self.gatt = device.connectGatt(self, False, self.callback)
        except Exception as e:
            self.log(f"connect error: {e}")

    def disconnect(self):
        try:
            if self.gatt:
                self.gatt.disconnect()
                self.gatt.close()
                self.gatt = None
                self.write_char = None
                self.notify_char = None
                self.status.text = "Disconnected"
        except Exception as e:
            self.log(f"disconnect error: {e}")

    def show_services(self):
        self.log("Known KuKirin Manager UUIDs found in the supplied APK:")
        self.log("0000fff0-0000-1000-8000-00805f9b34fb")
        self.log("0000fff1-0000-1000-8000-00805f9b34fb")
        self.log("0000fff2-0000-1000-8000-00805f9b34fb")
        self.log("00008877-0000-1000-8000-00805f9b34fb")
        self.log("00008888-0000-1000-8000-00805f9b34fb")

    def send_hex(self):
        raw = self.manual.text.strip().replace(",", " ")
        try:
            self.send(bytes.fromhex(raw))
        except Exception as e:
            self.log(f"HEX error: {e}")

    def send(self, payload):
        if not self.gatt or not self.write_char:
            self.log("Not connected / no writable characteristic.")
            return
        try:
            self.write_char.setValue(payload)
            ok = self.gatt.writeCharacteristic(self.write_char)
            self.log(f"TX: {payload.hex(' ').upper()} result={ok}")
        except Exception as e:
            self.log(f"write error: {e}")

if __name__ == "__main__":
    Main().run()
