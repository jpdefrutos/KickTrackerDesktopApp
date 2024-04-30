from PySide2.QtCore import QObject, QTimer, Signal, Slot, QThread
from PySide2.QtNetwork import QAbstractSocket, QTcpSocket, QUdpSocket
from typing import Union
from constants import *
from enum import Enum


class CommunicationSocket(QObject):
    new_message_signal = Signal(tuple)
    new_status_signal = Signal(int)
    error_signal = Signal(QAbstractSocket.SocketError)

    def __init__(self, parent = None, alive_signal_interval: int=1000, patience_s: Union[int, float] = 5, socket_type: str = "UDP", alive_signal: bool = False) -> None:
        super().__init__(parent)

        assert socket_type in ["UDP", "TCP"], f"Invalid socket type {socket_type}"
        self.alive_signal = False if not alive_signal else (1 if socket_type == "TCP" else 0)
        self.socket = QTcpSocket(self) if socket_type == "TCP" else QUdpSocket(self)
        self.patientce = int(patience_s * 1000)

        self._alive_timer = QTimer(self, interval=alive_signal_interval)
        self._alive_timer.timeout.connect(self._send_alive_signal)
        self.run_alive_timer(False)

        self.socket.stateChanged.connect(self._on_stateChanged)
        self.socket.readyRead.connect(self._on_readyRead)
        self.socket.errorOccurred.connect(self._on_socket_error)

        self.message_manager = MessageBuilder()

        self.host_ip = None
        self.host_port = None

    def run_alive_timer(self, new_state: bool):
        if self.alive_signal:
            if new_state:
                self._alive_timer.start()
            else:
                self._alive_timer.stop()
        else:
            self._alive_timer.stop()

    @Slot()
    def _on_socket_error(self, error):
        print("Something is wrong")
        if error == QAbstractSocket.ConnectionRefusedError:
            print(f"Unable to send data to {self.host_port}@{self.host_ip}")
        elif error == QAbstractSocket.HostNotFoundError:
            print(f"Unable to find the board {self.host_port}@{self.host_ip}")
        else:
            print(f"Error communicating with {self.host_port}@{self.host_ip}")
            print(f"{error}")
        self.close_socket(True)
        self.error_signal.emit(error)

    @Slot(QAbstractSocket.SocketState)
    def _on_stateChanged(self, state):
        if state == QAbstractSocket.ConnectedState:
            print("We have connection!")
            self.run_alive_timer(True)
            self.new_status_signal.emit(LED_SOCKET_CLOSE)
        elif state == QAbstractSocket.UnconnectedState:
            print("We have been disconnected")
            self.close_socket()
            self.run_alive_timer(False)
            self.new_status_signal.emit(LED_SOCKET_WAITING)

    def open_socket(self, host_ip: str = None, host_port: int = None):
        if self.state == QAbstractSocket.ConnectedState:
            self.close_socket()

        self.socket.connectToHost(host_ip, host_port)

        if(not self.socket.waitForConnected(self.patientce)):
            raise TimeoutError(f"Cannot connect to {host_port}@{host_ip}")
        else:
            print(f"Connected to {host_port}@{host_ip}")
            self.run_alive_timer(True)
            self.host_ip = host_ip
            self.host_port = host_port

    def reopen_socket(self):
        if self.host_ip is None or self.host_port is None:
            raise ValueError("Cannot reconnect. Missing information from the previos host!")
        else:
            self.open_socket(self.host_ip, self.host_port)
            

    def close_socket(self, force: bool = False):
        if self.state == QAbstractSocket.ConnectedState:
            if force:
                self.socket.abort()
            else:
                self.socket.disconnectFromHost()
            print(f"Disconnected from {self.host_port}@{self.host_ip}")
        else:
            print("Socket already disconnected")

    @property
    def state(self):
        return self.socket.state()
    
    def send_message(self, message: str):
        if self.state == QAbstractSocket.ConnectedState:
            self.socket.write(message)
            print(f"Sent {message} to {self.host_port}@{self.host_ip}")

    @Slot()
    def _on_readyRead(self):
        ret_val = ()
        raw_message = self.socket.readAll()
        try:
            ret_val = self.message_manager.parse_message(raw_message)
        except TypeError as err:
            print(f"Error parsing {raw_message}\n{err}")
            return ret_val
        # print(f"Received the message {raw_message} from {self.host_ip}: TASK ID: {ret_val[0]}\tTASK PARAMS: {ret_val[2]}")
        self.new_message_signal.emit(ret_val)
        return ret_val
    
    @Slot()
    def _send_alive_signal(self):
        self.send_message(self.message_manager.build_alive_message())

    def getCommunicationAddress(self):
        return (self.socket.localAddress().toString(), self.socket.localPort())


class MessageBuilder():
    task_id = Enum("task_id", ["HAND_SHAKE", "PREPARE_ACQUISITION", "START_ACQUISITION", "STOP_ACQUISITION", "DATA_SAMPLE", "POWER_OFF", "ALIVE"], start=0)
    msg_structure = "{0};{1:d};{2};\0"
    def __init__(self) -> None:
        pass
    
    def build_alive_message(self, ip: str, port: int) -> str:
        return self.msg_structure.format(self.task_id.HAND_SHAKE.value, 0, "").encode("utf-8")

    def build_initial_handshake(self, ip: str, port: int) -> str:
        return self.msg_structure.format(self.task_id.HAND_SHAKE.value, 2, f"{ip}:{port}").encode("utf-8")

    def build_prepare_acquisition_message(self, timer: int = None) -> str:
        if timer is not None:
            ret_val = self.msg_structure.format(self.task_id.PREPARE_ACQUISITION.value, 1, timer)
        else:
            ret_val = self.msg_structure.format(self.task_id.PREPARE_ACQUISITION.value, 0, "")
        return ret_val.encode("utf-8")
    
    def build_start_acquisition_message(self) -> str:
        return self.msg_structure.format(self.task_id.START_ACQUISITION.value, 0, "").encode("utf-8")
    
    def build_stop_acquisition_message(self) -> str:
        return self.msg_structure.format(self.task_id.STOP_ACQUISITION.value, 0, "").encode("utf-8")
    
    def build_power_off_message(self) -> str:
        return self.msg_structure.format(self.task_id.POWER_OFF.value, 0, "").encode("utf-8")
    
    def build_alive_message(self) -> str:
        return self.msg_structure.format(self.task_id.ALIVE.value, 0, "").encode("utf-8")

    def parse_message(self, raw_message: str):
        try:
            raw_message = raw_message.decode()
        except AttributeError as err:
            pass
        tokens = raw_message.split(";")
        task_type = self.task_id(int(tokens[0]))
        task_num_params = int(tokens[1])
        task_params = []
        if task_num_params > 0:
            task_params = tokens[2].split(":")

        return task_type, task_num_params, task_params
    