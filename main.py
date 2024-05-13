from PySide2.QtCore import Qt, QThreadPool, Slot
from PySide2.QtWidgets import QApplication, QWidget, QMainWindow, QGridLayout, QHBoxLayout, QVBoxLayout, QPushButton, QLineEdit, QSlider, QRadioButton, QLabel, QFileDialog, QLayout, QGroupBox
from PySide2.QtNetwork import QAbstractSocket
from PySide2.QtGui import QIcon

from tqdm import tqdm

from pglive.sources.data_connector import DataConnector
from pglive.sources.live_plot import LiveLinePlot, LiveScatterPlot
from pglive.sources.live_plot_widget import LivePlotWidget
from pglive.sources.live_axis import LiveAxis
from pglive.kwargs import Axis, Crosshair
import pyqtgraph as pg

import sys
import os
import socket
import re
from enum import Enum
import numpy as np
import json
import csv

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

    GUI_STATE_VALUES = Enum("state", ["INIT", "SEARCH_BOARD", "COMMS_OPEN", "IDLE", "PREPARE_ACQ", "RECV_DATA", "COMMS_ERROR"])
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

        self.setWindowTitle("KickTracker")
        self.setWindowIcon(QIcon("assets/icon.svg"))

        plotter_layout = self._build_plotter_layout()
        data_control_gb = self._build_data_control_group_box()
        acquisition_control_gb = self._build_acquisition_control_group_box()
        comms_control_gb = self._build_communication_group_box()
        calibration_contrl_gb = self._build_calibration_group_box()

        # Base layout
        base_layout = QGridLayout()
        base_layout.addLayout(plotter_layout, 0, 0, 4, 2)
        base_layout.addWidget(data_control_gb, 0, 2, 1, 1)
        base_layout.addWidget(acquisition_control_gb, 1, 2, 1, 1)
        base_layout.addWidget(calibration_contrl_gb,2, 2, 1, 1)
        base_layout.addWidget(comms_control_gb, 3, 2, 1, 1)

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
        
        self.max_x = -100
        self.max_y = -100
        self.max_z = -100
        self.max_acc = -100

        self.units_multiplier = 1

    def _build_acquisition_control_group_box(self) -> QGroupBox:
        group_box = QGroupBox("Acquisition", parent=self)
        # Tasks layout
        self.start_stop_button = QPushButton('Start', parent=group_box)
        self.start_stop_button.setMaximumWidth(self.menu_width)
        self.start_stop_button.setEnabled(False)
        self.start_stop_button.setCheckable(True)
        self.start_stop_button.toggled.connect(self.start_stop_acquisition)

        length_title = QLabel('Timer:', parent=group_box)
        length_title.setMaximumWidth(length_title.sizeHint().width())
        self.acquisition_length = QLineEdit(parent=group_box)
        self.acquisition_length.setMaximumWidth(self.menu_width - length_title.sizeHint().width())
        self.acquisition_length.setEnabled(False)

        length_layout = QHBoxLayout()
        length_layout.addWidget(length_title)
        length_layout.addWidget(self.acquisition_length)

        layout = QVBoxLayout()
        layout.addWidget(self.start_stop_button)
        layout.addLayout(length_layout)
        layout.setSizeConstraint(QLayout.SetMaximumSize)
        layout.addStretch(1)

        group_box.setLayout(layout)
        return group_box

    def _build_calibration_group_box(self) -> QGroupBox:
        group_box = QGroupBox("Caibration", parent=self)

        board_weight_lbl = QLabel(parent=group_box)
        board_weight_lbl.setText("Board weight (g)")
        board_weight_lbl.setMaximumWidth(board_weight_lbl.sizeHint().width())

        self.board_weight = QLineEdit(parent=group_box)
        self.board_weight.setText("90")
        self.board_weight.setEnabled(True)
        self.board_weight.setMaximumWidth(self.menu_width - board_weight_lbl.sizeHint().width())
        
        support_weight_lbl = QLabel(parent=group_box)
        support_weight_lbl.setText("Support weight (g)")
        support_weight_lbl.setMaximumWidth(support_weight_lbl.sizeHint().width())

        self.support_weight = QLineEdit(parent=group_box)
        self.support_weight.setText("400")
        self.support_weight.setEnabled(True)
        self.support_weight.setMaximumWidth(self.menu_width - support_weight_lbl.sizeHint().width())

        self.show_force = QRadioButton("Show max force", parent=group_box)
        self.show_force.toggled.connect(self.toggle_units)

        layout_weights = QGridLayout()
        layout_weights.addWidget(board_weight_lbl, 0, 0)
        layout_weights.addWidget(self.board_weight, 0, 1)
        layout_weights.addWidget(support_weight_lbl, 1, 0)
        layout_weights.addWidget(self.support_weight, 1, 1)

        layout = QVBoxLayout()
        layout.addLayout(layout_weights)
        layout.addWidget(self.show_force)
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

        self.power_off = QPushButton('Power off', parent=group_box)
        self.power_off.setMaximumWidth(self.menu_width)
        self.power_off.setEnabled(True)

        layout = QVBoxLayout()
        layout.addWidget(reminder)
        # layout.addWidget(self.ip_address_input, Qt.AlignCenter)
        # layout.addWidget(self.port_input)
        # layout.addWidget(self.connection_button)
        layout.addWidget(self.search_button, alignment=Qt.AlignCenter)
        layout.addWidget(self.connection_led, alignment=Qt.AlignHCenter)
        layout.addWidget(self.power_off, alignment=Qt.AlignHCenter)
        layout.setSizeConstraint(QLayout.SetMaximumSize)
        layout.addStretch(1)

        group_box.setLayout(layout)
        return group_box

    def _build_plotter_layout(self, background='white'):
        x_curve = LiveLinePlot(pen="red",)
        y_curve = LiveLinePlot(pen="green")
        z_curve = LiveLinePlot(pen="blue")
        acc_curve = LiveLinePlot(pen="black")
        acc_curve_scatter = LiveScatterPlot(pen="black")

        self.data_connector_x = DataConnector(x_curve, 600, 100)
        self.data_connector_y = DataConnector(y_curve, 600, 100)
        self.data_connector_z = DataConnector(z_curve, 600, 100)
        self.data_connector_acc = DataConnector(acc_curve, 600, 100,)
        self.data_connector_acc_scatter = DataConnector(acc_curve_scatter, 600, 100)
        kwargs = {Crosshair.ENABLED: True,
                  Crosshair.LINE_PEN: pg.mkPen(color="red", width=1),
                  Crosshair.TEXT_KWARGS: {"color": "green"},
                  Axis.TICK_FORMAT: Axis.TIME}
        time_axis = LiveAxis("bottom", **kwargs)
        self.plot_widget = LivePlotWidget(parent=self, background=background, title="Sensor data", axisItem={"bottom": time_axis}, roll_on_tick=20)
        self.plot_widget.showGrid(x=True, y=True, alpha=0.3)
        self.plot_widget.setLabel("bottom", "Time", units="ms")
        self.plot_widget.setLabel("left", "Acceleration", units="m/s**2")
        self.plot_widget.addItem(x_curve)
        self.plot_widget.addItem(y_curve)
        self.plot_widget.addItem(z_curve)
        self.plot_widget.addItem(acc_curve)
        self.plot_widget.addItem(acc_curve_scatter)
        self.plot_widget.add_crosshair(crosshair_pen=pg.mkPen(color="red", width=1), crosshair_text_kwargs={"color": "green"})

        self.plot_widget.setMinimumWidth(800)
        self.plot_widget.setMinimumHeight(600)

        self.data_connector_x.pause()
        self.data_connector_y.pause()
        self.data_connector_z.pause()
        self.data_connector_acc.pause()
        self.data_connector_acc_scatter.pause()
        
        layout = QVBoxLayout()
        layout.addWidget(self.plot_widget)
        return layout

    def _build_data_control_group_box(self) -> QGroupBox:
        group_box = QGroupBox("Data", parent=self)
        
        max_values_layout = QGridLayout()
        self.max_values_boxes = dict()
        for i, (l, c) in enumerate([("X", "red"), ("Y", "green"), ("Z", "blue"), ("ACC", "black")]):
            lbl = QLabel(parent=group_box)
            lbl.setText(f"{l}: ")
            lbl.setStyleSheet("QLabel {color :" + c + " ; }")
            lbl.setMaximumWidth(lbl.sizeHint().width())

            val = QLineEdit(parent=group_box)
            val.setEnabled(False)
            val.setMaximumWidth(self.menu_width - lbl.sizeHint().width())
            self.max_values_boxes[l] = val
            max_values_layout.addWidget(lbl, i, 0)
            max_values_layout.addWidget(val, i, 1)

        self.export_button = QPushButton('Export data', parent=group_box)
        self.export_button.setMaximumWidth(self.menu_width)
        self.export_button.clicked.connect(self.export_data)
        clear_plot_button = QPushButton('Clear', parent=group_box)
        clear_plot_button.setMaximumWidth(self.menu_width)
        clear_plot_button.clicked.connect(self.clear_plot)

        layout = QVBoxLayout()
        layout.addWidget(self.export_button)
        layout.addWidget(clear_plot_button)
        layout.addLayout(max_values_layout)
        layout.setSizeConstraint(QLayout.SetMaximumSize)
        layout.addStretch(1)

        group_box.setLayout(layout)
        return group_box
    
    @Slot()
    def update_led_status(self, new_state):
        if new_state == LED_SOCKET_OPEN:
            self.COMMS_SOCKET_OPEN = True
            colour = "green"
            self.start_stop_button.setEnabled(True)
            self.acquisition_length.setEnabled(True)
        else:
            colour = "red"
            self.COMMS_SOCKET_OPEN = False
            self.start_stop_button.setEnabled(False)
            self.acquisition_length.setEnabled(False)
            if new_state == LED_SOCKET_WAITING:
                colour = "yellow"

        self.connection_led.setStyleSheet(f"background-color: {colour}")
      
    def detect_board(self):    
        if self.GUI_STATE == self.GUI_STATE_VALUES.INIT:
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
                    self.data_receiver_socket.open_socket(parsed_params[0], int(parsed_params[1]))

                    template_addr = '.'.join(parsed_params[0].split('.')[:3])
                    reply = self.message_builder.build_initial_handshake(*self.data_receiver_socket.getCommunicationAddress())
                    udp_socket.sendto(reply, (parsed_params[0], int(parsed_params[1])))
                    print(f"Sent reply: {reply}")

                    self.board_ip = parsed_params[0]
                    self.board_port = parsed_params[1]

                    self.update_gui_state(self.GUI_STATE_VALUES.COMMS_OPEN)
                    self.search_button.setText("Disconnect from board")
                    self.update_led_status(LED_SOCKET_OPEN)
            udp_socket.detach()
            udp_socket.close()
            del udp_socket
        
        elif self.GUI_STATE != self.GUI_STATE_VALUES.INIT:
            self.update_gui_state(self.GUI_STATE_VALUES.INIT)
            self.search_button.setText("Connect to board")
            self.update_led_status(LED_SOCKET_CLOSE)
            self.board_ip = None
            self.board_port = None
            self.data_receiver_socket.close_socket()
            self.communication_socket.close_socket()

    @Slot()
    def new_message_from_the_board(self, parsed_message: tuple):
        task_id, num_params, params = parsed_message

        if task_id == self.message_builder.task_id.PREPARE_AQUISITION:
            if self.GUI_STATE == self.GUI_STATE_VALUES.PREPARE_ACQ:
                self.update_gui_state(self.GUI_STATE_VALUES.RECV_DATA)
                # Launch the DataReceiverSocket
                self.data_receiver_socket.open_socket(params[0], int(params[1]))
                if self.data_receiver_socket.state == QAbstractSocket.ConnectedState:
                    self.communication_socket.send_message(self.message_builder.build_start_acquisition_message())
            else:
                raise ValueError(f"Received PREPARE ACQUISITION but STATE = {self.GUI_STATE}")
        
        elif task_id == self.message_builder.task_id.STOP_ACQUISITION:
            if self.GUI_STATE == self.GUI_STATE_VALUES.RECV_DATA:
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
        if task_id == self.message_builder.task_id.DATA_SAMPLE and self.start_stop_button.isChecked():
            data_vector = np.asarray([d.toFloat()[0] for d in data_vector])
            acc_vector = data_vector[1:]
            acc_timestamp = data_vector[0] / 1000.0
            total_acc = np.linalg.norm(np.asarray(acc_vector))

            self.data_connector_x.cb_append_data_point(acc_vector[0], acc_timestamp)
            self.data_connector_y.cb_append_data_point(acc_vector[1], acc_timestamp)
            self.data_connector_z.cb_append_data_point(acc_vector[2], acc_timestamp)
            self.data_connector_acc.cb_append_data_point(total_acc, acc_timestamp)
            self.data_connector_acc_scatter.cb_append_data_point(total_acc, acc_timestamp)

            self.max_x = acc_vector[0] if acc_vector[0] > self.max_x else self.max_x
            self.max_y = acc_vector[1] if acc_vector[1] > self.max_y else self.max_y
            self.max_z = acc_vector[2] if acc_vector[2] > self.max_z else self.max_z
            self.max_acc = total_acc if total_acc > self.max_acc else self.max_acc

            self.max_values_boxes["X"].setText(f"{self.max_x * self.units_multiplier}")
            self.max_values_boxes["Y"].setText(f"{self.max_y * self.units_multiplier}")
            self.max_values_boxes["Z"].setText(f"{self.max_z * self.units_multiplier}")
            self.max_values_boxes["ACC"].setText(f"{self.max_acc * self.units_multiplier}")

            for l in zip(["X", "Y", "Z", "ACC"], ()):
                self.max_values_boxes[l].setText()
            print(f"TS: {acc_timestamp} [{acc_vector}] {total_acc:.3f} m/s**2")

    @Slot()
    def start_stop_acquisition(self):
        if self.start_stop_button.isChecked():
            self.data_connector_x.resume()
            self.data_connector_y.resume()
            self.data_connector_z.resume()
            self.data_connector_acc.resume()
            self.data_connector_acc_scatter.resume()
            self.start_stop_button.setText("Stop")
        elif not self.start_stop_button.isChecked():
            self.data_connector_x.pause()
            self.data_connector_y.pause()
            self.data_connector_z.pause()
            self.data_connector_acc.pause()
            self.data_connector_acc_scatter.pause()
            self.start_stop_button.setText("Start")

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

    def export_data(self):
        assert len(self.data_connector_x.x), "Nothing to save"
        button_state_backup = self.start_stop_button.isChecked()
        self.start_stop_button.setChecked(False)
        self.start_stop_button.toggled.emit(False)

        file_path, _ = QFileDialog.getSaveFileName(self, "Save data to", os.path.expanduser("~"), "Supported formats (*.json, *.csv)")
        extension = file_path.split(".")[-1]
        
        with open(file_path, "w") as f:
            if extension.lower() == "json":
                data = {
                    "max_x": self.max_x / self.units_multiplier,
                    "max_y": self.max_y / self.units_multiplier,
                    "max_z": self.max_z / self.units_multiplier,
                    "max_acc": self.max_acc / self.units_multiplier,
                    "time_ms": np.asanyarray(self.data_connector_x.x).tolist(),
                    "x": np.asanyarray(self.data_connector_x.y).tolist(),
                    "y": np.asanyarray(self.data_connector_y.y).tolist(),
                    "z": np.asanyarray(self.data_connector_z.y).tolist(),
                    "acc": np.asanyarray(self.data_connector_acc.y).tolist(),
                }
                json.dump(data, f)
            elif extension.lower() == "csv":
                csv_writer = csv.writer(f, delimiter=";")
                csv_writer.writerow(["time_ms", "x", "y", "z", "acc"])
                csv_writer.writerows([[t, x, y, z, acc] for (t, x, y, z, acc) in zip(self.data_connector_x.x,
                                                                                     self.data_connector_x.y,
                                                                                     self.data_connector_y.y,
                                                                                     self.data_connector_z.y,
                                                                                     self.data_connector_acc.y)])
            else:
                raise ValueError(f"Unknown extesion {extension}")
            
        self.start_stop_button.isCheckable(button_state_backup)
        self.start_stop_button.toggled.emit(button_state_backup)

    def clear_plot(self):
        self.data_connector_x.clear()
        self.data_connector_y.clear()
        self.data_connector_z.clear()
        self.data_connector_acc.clear()
        self.data_connector_acc_scatter.clear()
        self.max_x = -100
        self.max_y = -100
        self.max_z = -100
        self.max_acc = -100
        self.max_values_boxes["X"].setText("")
        self.max_values_boxes["Y"].setText("")
        self.max_values_boxes["Z"].setText("")
        self.max_values_boxes["ACC"].setText("")

    def toggle_units(self):
        if self.show_force.isChecked():
            self.board_weight.setEnabled(False)
            self.support_weight.setEnabled(False)

            total_weight = (float(self.board_weight.text()) + float(self.support_weight.text())) / 1000
            self.units_multiplier = total_weight

        elif not self.show_force.isChecked():
            total_weight = (float(self.board_weight.text()) + float(self.support_weight.text())) / 1000
            self.units_multiplier = 1

            self.board_weight.setEnabled(True)
            self.support_weight.setEnabled(True)
        else:
            raise ValueError("Something is not right with the QRadiusButton")

if __name__ == '__main__':
    app = QApplication(sys.argv)

    window = MainInterface()
    window.show()

    app.exec_()