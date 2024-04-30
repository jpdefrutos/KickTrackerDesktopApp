from PySide2.QtCore import Qt, QThreadPool, QIODevice, Slot, Signal
from PySide2.QtWidgets import QApplication, QWidget, QMainWindow, QGridLayout, QHBoxLayout, QVBoxLayout, QPushButton, QLineEdit, QSlider, QRadioButton, QLabel, QSizePolicy, QLayout, QGroupBox
from PySide2.QtNetwork import QTcpSocket, QUdpSocket, QAbstractSocket, QHostInfo, QNetworkInterface
import time
import struct
from tqdm import tqdm

from pglive.sources.data_connector import DataConnector
from pglive.sources.live_plot import LiveLinePlot
from pglive.sources.live_plot_widget import LivePlotWidget
from pglive.sources.live_axis import LiveAxis
from pglive.kwargs import Axis
import sys
import socket
import re
from enum import Enum
import math
import numpy as np

import sys
sys.path.insert(0, './src')
from src.workers import Worker
from src.communication_tools import CommunicationSocket, MessageBuilder
from src.constants import *


def get_computer_IP(template_ip: str) -> str:
    ips = socket.gethostbyname_ex(socket.gethostname())[-1]
    temp = ".".join(template_ip.split(".")[:3])
    return_val = ""
    for ip in ips:
        if re.match(temp, ip):
            return_val = ip
            break

    return return_val


class MainInterface(QMainWindow):
    BOARD_FOUND = False
    COMMS_SOCKET_OPEN = False
    DATA_RECEIVER_SOCKET_OPEN = False
    BOARD_READY_TO_SEND_DATA = False

    GUI_STATE_VALUES = Enum("state", ["INIT", "SEARCH_BOARD", "COMMS_OPEN", "PREPARE_ACQ", "RECV_DATA", "COMMS_ERROR"])
    GUI_STATE = GUI_STATE_VALUES.INIT

    def __init__(self, parent=None, menu_width=250) -> None:
        super().__init__(parent=parent)
        self.plot_widget = None
        self.menu_width = menu_width

        # self.comms_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.message_builder = MessageBuilder()
        self.board_ip = None
        self.board_port = None
        self.update_gui_state(self.GUI_STATE_VALUES.INIT)

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

        # Threads
        self.threadpool = QThreadPool()
        print(f"Multithreading with maximum {self.threadpool.maxThreadCount()} threads")

        widget = QWidget()
        widget.setLayout(base_layout)
        self.setCentralWidget(widget)

        self.communication_socket = CommunicationSocket(socket_type="UDP")
        self.communication_socket.new_status_signal.connect(self.update_led_status)
        self.communication_socket.new_message_signal.connect(self.new_message_from_the_board)
        self.communication_socket.error_signal.connect(self.raise_communication_error)

        self.data_receiver_socket = CommunicationSocket(socket_type="UDP")
        self.data_receiver_socket.new_message_signal.connect(self.update_plot)

        self.data_receiver = CommunicationSocket(socket_type="UDP")

    def _build_acquisition_control_group_box(self) -> QGroupBox:
        group_box = QGroupBox("Acquisition", parent=self)
        # Tasks layout
        self.start_button = QPushButton('Start', parent=group_box)
        self.start_button.setMaximumWidth(self.menu_width)
        self.start_button.setEnabled(False)
        self.start_button.clicked.connect(self.start_acquisition)

        self.stop_button = QPushButton('Force stop', parent=group_box)
        self.stop_button.setMaximumWidth(self.menu_width)
        self.stop_button.setEnabled(False)
        self.stop_button.clicked.connect(self.stop_acquisition)

        length_title = QLabel('Timer:', parent=group_box)
        length_title.setMaximumWidth(length_title.sizeHint().width())
        self.acquisition_length = QLineEdit(parent=group_box)
        self.acquisition_length.setMaximumWidth(self.menu_width - length_title.sizeHint().width())
        self.acquisition_length.setEnabled(False)

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
        reminder = QLabel(parent=group_box)
        reminder.setText(f"Remember to connect to '{BOARD_SSID}'")
        # self.ip_address_input = QLineEdit(parent=group_box)
        # self.ip_address_input.setPlaceholderText("IP address")
        # self.ip_address_input.setMaximumWidth(self.menu_width)
        # self.port_input = QLineEdit(parent=group_box)
        # self.port_input.setPlaceholderText("Port")
        # self.port_input.setMaximumWidth(self.menu_width)
        # self.connection_button = QPushButton('Connect', parent=group_box)
        # self.connection_button.setMaximumWidth(self.menu_width)
        # self.connection_button.pressed.connect(self.toggle_connection)

        self.search_button = QPushButton("Search for board", parent=group_box)
        self.search_button.setMaximumWidth(self.menu_width)
        self.search_button.pressed.connect(self.detect_board)

        self.connection_led = QPushButton(parent=group_box)
        self.connection_led.resize(50, 50)
        self.connection_led.setEnabled(False)
        self.update_led_status(LED_SOCKET_CLOSE)

        layout = QVBoxLayout()
        layout.addWidget(reminder)
        # layout.addWidget(self.ip_address_input, Qt.AlignCenter)
        # layout.addWidget(self.port_input)
        # layout.addWidget(self.connection_button)
        layout.addWidget(self.search_button, alignment=Qt.AlignCenter)
        layout.addWidget(self.connection_led, alignment=Qt.AlignHCenter)
        layout.setSizeConstraint(QLayout.SetMaximumSize)
        layout.addStretch(1)

        group_box.setLayout(layout)
        return group_box

    def _build_plotter_layout(self, background='white'):
        x_curve = LiveLinePlot(pen="red")
        y_curve = LiveLinePlot(pen="green")
        z_curve = LiveLinePlot(pen="blue")
        acc_curve = LiveLinePlot(pen="black")

        self.data_connector_x = DataConnector(x_curve, 600, 100)
        self.data_connector_y = DataConnector(y_curve, 600, 100)
        self.data_connector_z = DataConnector(z_curve, 600, 100)
        self.data_connector_acc = DataConnector(acc_curve, 600, 100)

        time_axis = LiveAxis("bottom", **{Axis.TICK_FORMAT: Axis.TIME})
        self.plot_widget = LivePlotWidget(parent=self, title="Acceleration", axisItem={"bottom": time_axis})
        self.plot_widget.showGrid(x=True, y=True, alpha=0.3)
        self.plot_widget.setLabel("bottom", "Time", units="ms")
        self.plot_widget.setLabel("left", "Acceleration", units="m/s**2")
        self.plot_widget.addItem(x_curve)
        self.plot_widget.addItem(y_curve)
        self.plot_widget.addItem(z_curve)
        self.plot_widget.addItem(acc_curve)

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
    
    @Slot()
    def update_led_status(self, new_state):
        if new_state == LED_SOCKET_OPEN:
            self.COMMS_SOCKET_OPEN = True
            colour = "green"
            self.start_button.setEnabled(True)
            self.stop_button.setEnabled(True)
            self.acquisition_length.setEnabled(True)
        else:
            colour = "red"
            self.COMMS_SOCKET_OPEN = False
            self.start_button.setEnabled(False)
            self.stop_button.setEnabled(False)
            self.acquisition_length.setEnabled(False)
            if new_state == LED_SOCKET_WAITING:
                colour = "yellow"

        self.connection_led.setStyleSheet(f"background-color: {colour}")
      
    def detect_board(self):
        if self.GUI_STATE != self.GUI_STATE_VALUES.INIT:
            self.update_gui_state(self.GUI_STATE_VALUES.INIT)
            self.search_button.setText("Search for board")
            self.update_led_status(LED_SOCKET_CLOSE)
            self.board_ip = None
            self.board_port = None
            
        elif self.GUI_STATE == self.GUI_STATE_VALUES.INIT:
            self.update_gui_state(self.GUI_STATE_VALUES.SEARCH_BOARD)
            udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                # Set socket options to allow receiving broadcasted messages
            udp_socket.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            udp_socket.bind(('', 3500))

            print('Waiting for message...')
            data, addr = udp_socket.recvfrom(1024)
            
            if data:
                print(f"Received {data} from {addr}")
                parsed_task_id, _, parsed_params = self.message_builder.parse_message(data)
                if parsed_task_id == self.message_builder.task_id.HAND_SHAKE:
                    self.update_gui_state(self.GUI_STATE_VALUES.COMMS_OPEN)
                    self.communication_socket.open_socket(parsed_params[0], int(parsed_params[1]))
                    
                    template_addr = '.'.join(parsed_params[0].split('.')[:3])
                    reply = self.message_builder.build_initial_handshake(*self.communication_socket.getCommunicationAddress())
                    self.communication_socket.send_message(reply)
                    self.board_ip = parsed_params[0]
                    self.board_port = parsed_params[1]

                    
                    # self.communication_socket.send_message(self.message_builder.build_alive_message())
                    self.update_led_status(LED_SOCKET_WAITING)
            udp_socket.detach()
            udp_socket.close()

    @Slot()
    def new_message_from_the_board(self, parsed_message: tuple):
        task_id, num_params, params = parsed_message

        if task_id == self.message_builder.task_id.PREPARE_AQUISITION:
            if self.GUI_STATE == self.GUI_STATE_VALUES.PREPARE_ACQ:
                self.update_gui_state(self.GUI_STATE_VALUES.RECV_DATA)
                self.stop_button.setEnabled(True)
                # Launch the DataReceiverSocket
                self.data_receiver_socket.open_socket(params[0], int(params[1]))
                if self.data_receiver_socket.state == QAbstractSocket.ConnectedState:
                    self.communication_socket.send_message(self.message_builder.build_start_acquisition_message())
            else:
                raise ValueError(f"Received PREPARE ACQUISITION but STATE = {self.GUI_STATE}")
        
        elif task_id == self.message_builder.task_id.STOP_ACQUISITION:
            if self.GUI_STATE == self.GUI_STATE_VALUES.RECV_DATA:
                self.stop_button.setEnabled(False)
                self.data_receiver_socket.close_socket()
                self.update_gui_state(self.GUI_STATE_VALUES.COMMS_OPEN)

        elif task_id == self.message_builder.task_id.HAND_SHAKE:
            if self.GUI_STATE == self.GUI_STATE_VALUES.SEARCH_BOARD:
                self.update_gui_state(self.GUI_STATE_VALUES.COMMS_OPEN)
                self.update_led_status(LED_SOCKET_OPEN)
            else:
                pass
        else:
            print(f"Received task ID {task_id}, and I don't know what to do with it")

    @Slot()
    def update_plot(self, new_data_sample):
        task_id, num_params, data_vector = new_data_sample
        if task_id == self.message_builder.task_id.DATA_SAMPLE:
            acc_vector = data_vector[:3]
            acc_timestamp = data_vector[-1]
            total_acc = np.linalg.norm(np.asarray(acc_vector))

            self.data_connector_x.cb_append_data_point(acc_vector[0], acc_timestamp)
            self.data_connector_y.cb_append_data_point(acc_vector[1], acc_timestamp)
            self.data_connector_z.cb_append_data_point(acc_vector[2], acc_timestamp)
            self.data_connector_acc.cb_append_data_point(total_acc, acc_timestamp)

            print(f"TS: {acc_timestamp} [{acc_vector}] {total_acc:.3f} m/s**2")

    @Slot()
    def start_acquisition(self):
        if self.GUI_STATE == self.GUI_STATE_VALUES.COMMS_OPEN:
            acq_length = None
            if self.acquisition_length.text() != "":
                acq_length = int(self.acquisition_length.text())
            msg = self.message_builder.build_prepare_acquisition_message(acq_length)
            self.communication_socket.send_message(msg)
            self.update_gui_state(self.GUI_STATE_VALUES.PREPARE_ACQ)

    @Slot()
    def stop_acquisition(self):
        if self.GUI_STATE == self.GUI_STATE_VALUES.RECV_DATA:
            msg = self.message_builder.build_stop_acquisition_message()
            self.communication_socket.send_message(msg)


    @Slot()
    def raise_communication_error(self, error):
        self.update_gui_state(self.GUI_STATE_VALUES.COMMS_ERROR)
        self.detect_board()
        self.data_receiver_socket.close_socket(True)
        raise ValueError(error)
    
    def update_gui_state(self, new_state):
        old_state = self.GUI_STATE
        print(f"New state: {old_state} -> {new_state}")
        self.GUI_STATE = new_state
        # TODO: Implement a new_state check
            

if __name__ == '__main__':
    app = QApplication(sys.argv)

    window = MainInterface()
    window.show()

    app.exec_()