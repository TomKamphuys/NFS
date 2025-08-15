"""
   Licensed under the Apache License, Version 2.0 (the "License");
   you may not use this file except in compliance with the License.
   You may obtain a copy of the License at

       http://www.apache.org/licenses/LICENSE-2.0

   Unless required by applicable law or agreed to in writing, software
   distributed under the License is distributed on an "AS IS" BASIS,
   WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
   See the License for the specific language governing permissions and
   limitations under the License.
"""

import sys

from remi import gui
from remi import start, App

from nfs import NearFieldScannerFactory
from scanner import ScannerFactory


class NFSApp(App):
    """
    A graphical user interface application for controlling a Near-Field Scanner
    (NFS) and performing operations like movement and measurements.

    This class, inheriting from `App`, is designed to facilitate
    interaction with a near-field scanner through an intuitive graphical
    interface containing buttons, labels, and containers. Users can perform
    various movements and measurements, reset the scanner's position, and exit the
    application. The GUI layout is dynamically created with vertical and horizontal
    containers housing interactive elements.

    :ivar lbl: A label widget to display the last executed command's description.
    :type lbl: gui.Label
    :ivar counter: A label widget to potentially display counter information,
                   currently unused.
    :type counter: gui.Label
    :ivar move_out_10_btn: A button for moving the scanner outwards by 10 mm.
    :type move_out_10_btn: gui.Button
    :ivar move_out_1_btn: A button for moving the scanner outwards by 1 mm.
    :type move_out_1_btn: gui.Button
    :ivar move_in_1_btn: A button for moving the scanner inwards by 1 mm.
    :type move_in_1_btn: gui.Button
    :ivar move_in_10_btn: A button for moving the scanner inwards by 10 mm.
    :type move_in_10_btn: gui.Button
    :ivar move_up_10_btn: A button for moving the scanner upward by 10 mm.
    :type move_up_10_btn: gui.Button
    :ivar move_up_1_btn: A button for moving the scanner upward by 1 mm.
    :type move_up_1_btn: gui.Button
    :ivar move_down_1_btn: A button for moving the scanner downward by 1 mm.
    :type move_down_1_btn: gui.Button
    :ivar move_down_10_btn: A button for moving the scanner downward by 10 mm.
    :type move_down_10_btn: gui.Button
    :ivar zero_btn: A button to reset the scanner to its zero position.
    :type zero_btn: gui.Button
    :ivar take_measurement_btn: A button to trigger a single measurement process
                                using the scanner.
    :type take_measurement_btn: gui.Button
    :ivar take_measurement_set_btn: A button to trigger a set of measurements
                                    using the scanner.
    :type take_measurement_set_btn: gui.Button
    :ivar exit_btn: A button to exit the application gracefully by invoking the
                    `on_close` method.
    :type exit_btn: gui.Button
    :ivar stop_flag: A flag utilized to control the termination of some
                     background timers or processes.
    :type stop_flag: bool
    :ivar scanner: An object providing low-level control of the scanner's
                   movement operations.
    :type scanner: Scanner
    :ivar nfs: An instance of NearFieldScanner, responsible for higher-level
               measurement tasks interfacing with the scanner.
    :type nfs: NearFieldScanner
    :ivar sub_container_right: A container that holds the right-most interface
                               elements like buttons and labels for scanner
                               operation.
    :type sub_container_right: gui.Container
    """

    def __init__(self, *args):
        super().__init__(*args)

    def idle(self):
        pass

    def main(self):
        # the margin 0px auto centers the main container
        vertical_container = gui.Container(width=540, margin='0px auto',
                                           style={'display': 'block', 'overflow': 'hidden'})

        horizontal_container = gui.Container(width='100%', layout_orientation=gui.Container.LAYOUT_HORIZONTAL,
                                             margin='0px', style={'display': 'block', 'overflow': 'auto'})

        sub_container_right = gui.Container(
            style={'width': '220px', 'display': 'block', 'overflow': 'auto', 'text-align': 'center'})
        self.count = 0
        self.counter = gui.Label('', width=200, height=30, margin='10px')

        # label is used to state last command
        self.lbl = gui.Label('Welcome!', width=200, height=30, margin='10px')

        self.move_out_10_btn = gui.Button('Out 10 mm', width=100, height=30, margkin='10px')
        self.move_out_10_btn.onclick.do(self.move_out_10)

        self.move_out_1_btn = gui.Button('Out 1 mm', width=100, height=30, margkin='10px')
        self.move_out_1_btn.onclick.do(self.move_out_1)

        self.move_in_1_btn = gui.Button('In 1 mm', width=100, height=30, margkin='10px')
        self.move_in_1_btn.onclick.do(self.move_in_1)

        self.move_in_10_btn = gui.Button('In 10 mm', width=100, height=30, margkin='10px')
        self.move_in_10_btn.onclick.do(self.move_in_10)

        self.move_up_10_btn = gui.Button('Up 10 mm', width=100, height=30, margkin='10px')
        self.move_up_10_btn.onclick.do(self.move_up_10)

        self.move_up_1_btn = gui.Button('Up 1 mm', width=100, height=30, margkin='10px')
        self.move_up_1_btn.onclick.do(self.move_up_1)

        self.move_down_1_btn = gui.Button('Down 1 mm', width=100, height=30, margkin='10px')
        self.move_down_1_btn.onclick.do(self.move_down_1)

        self.move_down_10_btn = gui.Button('Down 10 mm', width=100, height=30, margkin='10px')
        self.move_down_10_btn.onclick.do(self.move_down_10)

        self.zero_btn = gui.Button('Zero scanner', width=100, height=30, margkin='10px')
        self.zero_btn.onclick.do(self.zero)

        self.take_measurement_btn = gui.Button('take single measurement', width=100, height=30, margkin='10px')
        self.take_measurement_btn.onclick.do(self.take_single_measurement)

        self.take_measurement_set_btn = gui.Button('take measurement set', width=100, height=30, margkin='10px')
        self.take_measurement_set_btn.onclick.do(self.take_measurement_set)

        self.exit_btn = gui.Button('Exit', width=100, height=30, margkin='10px')
        self.exit_btn.onclick.do(self.exit)

        # appending a widget to another, the first argument is a string key
        sub_container_right.append([self.counter, self.lbl,
                                    self.move_out_10_btn, self.move_out_1_btn, self.move_in_1_btn, self.move_in_10_btn,
                                    self.move_up_10_btn, self.move_up_1_btn, self.move_down_1_btn,
                                    self.move_down_10_btn,
                                    self.zero_btn,
                                    self.take_measurement_btn, self.take_measurement_set_btn,
                                    self.exit_btn])

        self.sub_container_right = sub_container_right

        horizontal_container.append([sub_container_right])

        vertical_container.append([horizontal_container])

        # this flag will be used to stop the display_counter Timer
        self.stop_flag = False

        config_file = './config.ini'
        self.scanner = ScannerFactory.create(config_file)

        self.nfs = NearFieldScannerFactory.create(self.scanner, config_file)

        # returning the root widget
        return vertical_container

    # listener function
    def move_out_10(self, widget):
        self.lbl.set_text('Moving out 10 mm')
        self.scanner.move_out(10)

    def move_out_1(self, widget):
        self.lbl.set_text('Moving out 1 mm')
        self.scanner.move_out(1)

    def move_in_1(self, widget):
        self.lbl.set_text('Moving in 1 mm')
        self.scanner.move_in(1)

    def move_in_10(self, widget):
        self.lbl.set_text('Moving in 10 mm')
        self.scanner.move_in(10)

    def move_up_10(self, widget):
        self.lbl.set_text('Moving up 10 mm')
        self.scanner.move_up(10)

    def move_up_1(self, widget):
        self.lbl.set_text('Moving up 1 mm')
        self.scanner.move_up(1)

    def move_down_1(self, widget):
        self.lbl.set_text('Moving down 1 mm')
        self.scanner.move_down(1)

    def move_down_10(self, widget):
        self.lbl.set_text('Moving in down mm')
        self.scanner.move_down(10)

    def zero(self, widget):
        self.lbl.set_text('Zero scanner')
        self.scanner.set_as_zero()

    def take_single_measurement(self, widget):
        self.lbl.set_text('Take single measurement')
        self.nfs.take_single_measurement()

    def take_measurement_set(self, widget):
        self.lbl.set_text('Take measurement set')
        self.nfs.take_measurement_set()

    def exit(self, widget):
        print("on_close_button")
        self.on_close()

    def on_close(self):
        print("on_close start")
        super(NFSApp, self).on_close()
        # self.server.server_starter_instance._alive = False
        # self.server.server_starter_instance._sserver.shutdown()
        print("on_close end")
        sys.exit()


if __name__ == "__main__":
    # starts the webserver
    # optional parameters
    # start(MyApp,address='127.0.0.1', port=8081, multiple_instance=False,enable_file_cache=True, update_interval=0.1, start_browser=True)
    start(NFSApp, debug=True, address='0.0.0.0', port=8083, start_browser=True, multiple_instance=True)
