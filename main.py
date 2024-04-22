from PySide2.QtCore import Qt
from PySide2.QtWidgets import QApplication, QWidget, QMainWindow, QGridLayout, QHBoxLayout, QVBoxLayout, QPushButton, QLineEdit, QSlider, QRadioButton, QLabel, QSizePolicy, QLayout, QGroupBox
from PySide2.QtNetwork import QTcpSocket, QUdpSocket, QAbstractSocket, QHostInfo

import pyqtgraph
import sys
import socket
import re
from enum import Enum

class MessageBuilder():
    task_id = Enum('task_id', ['START_ACQUISITION', 'STOP_ACQUISITION', 'TIMED_ACQUISITION', 'GET_CONFIGURATION', 'UPDATE_CONFIGURATION', 'POWER_OFF', 'ALIVE', 'INIT'])
    msg_structure = '{0};{1:d};{2};\0'
    def __init__(self) -> None:
        pass

    def build_initial_handshake(self) -> str:
        return self.msg_structure.format(self.task_id.INIT, 0, '').encode('utf-8')

    def build_start_acquisition_message(self, timer: int = None) -> str:
        if timer is not None:
            ret_val = self.msg_structure.format(self.task_id.START_ACQUISITION, 1, timer)
        else:
            ret_val = self.msg_structure.format(self.task_id.START_ACQUISITION, 0, '')
        return ret_val.encode('utf-8')
    
    def build_start_timed_acquisition_message(self, timer: int) -> str:
        return self.build_start_acquisition_message(timer=timer)
    
    def build_stop_acquisition_message(self) -> str:
        return self.msg_structure.format(self.task_id.STOP_ACQUISITION, 0, '').encode('utf-8')
    
    def build_get_configuration_message(self) -> str:
        return self.msg_structure.format(self.task_id.GET_CONFIGURATION, 0, '').encode('utf-8')
    
    def build_update_configuration_message(self, new_configuration: dict) -> str:
        assert ['ssid', 'ip', 'port'] in new_configuration.keys, '[ERR] Invalid configuration dictionary'
        return self.msg_structure.format(self.task_id.UPDATE_CONFIGURATION, 3, f'{new_configuration["ssid"]}:{new_configuration["ip"]}:{new_configuration["port"]}').encode('utf-8')
    
    def build_power_off_message(self) -> str:
        return self.msg_structure.format(self.task_id.POWER_OFF, 0, '').encode('utf-8')
    
    def build_alive_message(self) -> str:
        return self.msg_structure.format(self.task_id.ALIVE, 0, '').encode('utf-8')

    def parse_message(self, raw_message: str):
        tokens = raw_message.split(';')
        task_type = self.task_id[tokens[0]]
        task_num_params = int(tokens[1])
        task_params = []
        if task_num_params > 0:
            task_params = tokens[2].split(':')

        return task_type, task_num_params, task_params
    

class MainInterface(QMainWindow):
    def __init__(self, parent=None, menu_width=250) -> None:
        super().__init__(parent=parent)
        self.plot_widget = None
        self.menu_width = menu_width

        # self.comms_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.message_builder = MessageBuilder()
        self.board_ip = None
        self.board_port = None

        self.setWindowTitle("Kick Sensor")

        plotter_layout = self._build_plotter_layout()
        data_control_gb = self._build_data_control_group_box()
        acquisition_control_gb = self._build_acquisition_control_group_box()
        comms_control_gb = self._build_communication_group_box()

        # Base layout
        base_layout = QGridLayout()
        base_layout.addLayout(plotter_layout, 0, 0, 3, 2)
        base_layout.addWidget(data_control_gb, 0, 2, 1, 1)
        base_layout.addWidget(acquisition_control_gb, 1, 2, 1, 1)
        base_layout.addWidget(comms_control_gb, 2, 2, 1, 1)


        widget = QWidget()
        widget.setLayout(base_layout)
        self.setCentralWidget(widget)

        self.communication_client = QTcpSocket(parent=self)
        self.data_receiver = QUdpSocket(parent=self)

    def _build_acquisition_control_group_box(self) -> QGroupBox:
        group_box = QGroupBox("Acquisition", parent=self)
        # Tasks layout
        self.start_button = QPushButton('Start', parent=group_box)
        self.start_button.setMaximumWidth(self.menu_width)
        self.stop_button = QPushButton('Force stop', parent=group_box)
        self.stop_button.setMaximumWidth(self.menu_width)

        length_title = QLabel('Timer:', parent=group_box)
        length_title.setMaximumWidth(length_title.sizeHint().width())
        self.acquisition_length = QLineEdit(parent=group_box)
        self.acquisition_length.setMaximumWidth(self.menu_width - length_title.sizeHint().width())

        length_layout = QHBoxLayout()
        length_layout.addWidget(length_title)
        length_layout.addWidget(self.acquisition_length)

        layout = QVBoxLayout()
        layout.addWidget(self.start_button)
        layout.addLayout(length_layout)
        layout.addWidget(self.stop_button)
        layout.setSizeConstraint(QLayout.SetMaximumSize)
        layout.addStretch(1)

        group_box.setLayout(layout)
        return group_box

    def _build_communication_group_box(self) -> QGroupBox:
        group_box = QGroupBox("Communication", parent=self)
        # Communications layout
        self.ip_address_input = QLineEdit(parent=group_box)
        self.ip_address_input.setPlaceholderText('IP address')
        self.ip_address_input.setMaximumWidth(self.menu_width)
        self.port_input = QLineEdit(parent=group_box)
        self.port_input.setPlaceholderText('Port')
        self.port_input.setMaximumWidth(self.menu_width)
        self.connection_button = QPushButton('Connect', parent=group_box)
        self.connection_button.setMaximumWidth(self.menu_width)
        self.connection_button.pressed.connect(self.connect_to_board)
        self.connection_led = QPushButton(parent=group_box)
        self.connection_led.resize(50, 50)
        self.connection_led.setStyleSheet('background-color: red')
        self.connection_active = False

        layout = QVBoxLayout()
        layout.addWidget(self.ip_address_input, Qt.AlignCenter)
        layout.addWidget(self.port_input)
        layout.addWidget(self.connection_button)
        layout.addWidget(self.connection_led, alignment=Qt.AlignHCenter)
        layout.setSizeConstraint(QLayout.SetMaximumSize)
        layout.addStretch(1)

        group_box.setLayout(layout)
        return group_box
    
    def _build_plotter_layout(self, background='white') -> pyqtgraph.PlotWidget:
        self.plot_widget = pyqtgraph.PlotWidget(background=background, parent=self)
        self.plot_widget.setMinimumWidth(800)
        self.plot_widget.setMinimumHeight(600)

        self.plot_slider = QSlider(Qt.Horizontal, parent=self)
        self.plot_slider.setEnabled(False)

        layout = QVBoxLayout()
        layout.addWidget(self.plot_widget)
        layout.addWidget(self.plot_slider)
        return layout

    def _build_data_control_group_box(self) -> QGroupBox:
        group_box = QGroupBox("Data", parent=self)
        show_min_max = QRadioButton('Show min/max', parent=group_box)
        export_button = QPushButton('Export data', parent=group_box)
        export_button.setMaximumWidth(self.menu_width)
        clear_plot = QPushButton('Clear', parent=group_box)
        clear_plot.setMaximumWidth(self.menu_width)

        layout = QVBoxLayout()
        layout.addWidget(show_min_max)
        layout.addWidget(export_button)
        layout.addWidget(clear_plot)
        layout.setSizeConstraint(QLayout.SetMaximumSize)
        layout.addStretch(1)

        group_box.setLayout(layout)
        return group_box
    
    def connect_to_board(self):
        self.ip_address_input.setEnabled(False)
        self.port_input.setEnabled(False)

        self.board_ip = self.ip_address_input.text()
        self.board_port = int(self.port_input.text())

        assert re.match('+\d\.+\d\.+\d\.+\d', self.board_ip), '[ERR] Invalid IP format'
        assert self.board_port > 0, '[ERR] Invalid port number'

        self.communication_client.abort()
        self.communication_client.connectToHost(self.board_ip, self.board_port)
        self.communication_client.readReady.connect(self._read_tcp_socket)
        self.communication_client.error.connect(self._on_tcp_error)
        self.communication_client.connected.connect(self._on_tcp_connected)

        # with self.comms_socket as s:
        #     s.connect((ip, port))
        #     s.sendall(self.message_builder.build_alive_message)
        #     assert self.message_builder.parse_message(s.recv(1024))[0] == self.message_builder.task_id.ALIVE, '[ERR] Cannot communicate with the board'            
        #     self.connection_led.setStyleSheet('background-color: green')
        #     self.connection_active = True
        return
    
    def send_message(self, message: str):
        with self.comms_socket as s:
            s.connect((self.ip_address_input.text(), int(self.port_input.text())))
            s.sendall(message)
            if self.message_builder.parse_message(s.recv(1024))[0] == self.message_builder.task_id.ALIVE:
                print('[ERR] ACK not received')

    def _read_tcp_socket(self):
        raw_message = self.communication_client.readAll()
        task_id, task_num_params, task_params = self.message_builder.parse_message(raw_message)
        print(f'Received the message {raw_message}: TASK ID: {task_id}\tTASK PARAMS: {task_params}')
        return
    
    def _on_tcp_error(self, error):
        if error == QAbstractSocket.ConnectionRefusedError:
            print(f'Unable to send data to {self.board_port}@{self.board_ip}')
    
    def _on_tcp_connected(self):
        self.connection_active = True
        self.communication_client.write(self.message_builder.build_initial_handshake())
        self.communication_client.flush()


    def _read_udp_socket(self):
        return


if __name__ == '__main__':
    app = QApplication(sys.argv)

    window = MainInterface()
    window.show()

    app.exec_()