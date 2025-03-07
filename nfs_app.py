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

from remi import gui
from remi import start, App
import sys
from nfs import NearFieldScannerFactory


class NFSApp(App):
    """
    Represents a Near Field Scanner application for controlling hardware movements, performing
    measurements, and managing a user interface for scanner operations.

    The class is designed to support various scanner movement commands through UI buttons and
    provides an interactive interface that allows users to control a Near Field Scanner for
    precise operations. It integrates with a GUI framework and scanner factory to provide
    functionalities such as measurements, resetting scanner position to zero, and exiting
    the application.

    :ivar count: Internal counter used for tracking application-specific operations.
    :type count: int
    :ivar counter: GUI label widget for displaying counter value.
    :type counter: gui.Label
    :ivar lbl: GUI label widget for displaying the last command executed.
    :type lbl: gui.Label
    :ivar move_out_10_btn: GUI button for moving the scanner outwards by 10 mm.
    :type move_out_10_btn: gui.Button
    :ivar move_out_1_btn: GUI button for moving the scanner outwards by 1 mm.
    :type move_out_1_btn: gui.Button
    :ivar move_in_1_btn: GUI button for moving the scanner inwards by 1 mm.
    :type move_in_1_btn: gui.Button
    :ivar move_in_10_btn: GUI button for moving the scanner inwards by 10 mm.
    :type move_in_10_btn: gui.Button
    :ivar move_up_10_btn: GUI button for moving the scanner upwards by 10 mm.
    :type move_up_10_btn: gui.Button
    :ivar move_up_1_btn: GUI button for moving the scanner upwards by 1 mm.
    :type move_up_1_btn: gui.Button
    :ivar move_down_1_btn: GUI button for moving the scanner downwards by 1 mm.
    :type move_down_1_btn: gui.Button
    :ivar move_down_10_btn: GUI button for moving the scanner downwards by 10 mm.
    :type move_down_10_btn: gui.Button
    :ivar zero_btn: GUI button for resetting the scanner position to zero.
    :type zero_btn: gui.Button
    :ivar take_measurement_btn: GUI button for taking a single measurement.
    :type take_measurement_btn: gui.Button
    :ivar take_measurement_set_btn: GUI button for taking a set of measurements.
    :type take_measurement_set_btn: gui.Button
    :ivar exit_btn: GUI button for exiting the application.
    :type exit_btn: gui.Button
    :ivar sub_container_right: GUI container for holding various buttons and labels.
    :type sub_container_right: gui.Container
    :ivar stop_flag: Flag indicating whether to stop the timer for display_counter.
    :type stop_flag: bool
    :ivar nfs: Instance of the NearFieldScannerFactory for managing the scanner operations.
    :type nfs: NearFieldScannerFactory
    :ivar scanner: Scanner object created by the NearFieldScannerFactory instance.
    :type scanner: NearFieldScanner
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
                                  self.move_up_10_btn, self.move_up_1_btn, self.move_down_1_btn, self.move_down_10_btn,
                                  self.zero_btn,
                                  self.take_measurement_btn, self.take_measurement_set_btn,
                                  self.exit_btn])

        self.sub_container_right = sub_container_right

        horizontal_container.append([sub_container_right])

        vertical_container.append([horizontal_container])

        # this flag will be used to stop the display_counter Timer
        self.stop_flag = False

        self.nfs = NearFieldScannerFactory().create('./config.ini')
        self.scanner = self.nfs.scanner

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
    start(NFSApp, debug=True, address='0.0.0.0', port=8081, start_browser=True, multiple_instance=True)
