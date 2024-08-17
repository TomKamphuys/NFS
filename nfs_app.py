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

import remi.gui as gui
from remi import start, App
from nfs import NearFieldScannerFactory


class NFSApp(App):
    def __init__(self, *args):
        super(NFSApp, self).__init__(*args)

    def idle(self):
        pass

    def main(self):
        self.nfs = NearFieldScannerFactory().create('./config.ini')
        self.scanner = self.nfs._scanner

        # the margin 0px auto centers the main container
        verticalContainer = gui.Container(width=540, margin='0px auto',
                                          style={'display': 'block', 'overflow': 'hidden'})

        horizontalContainer = gui.Container(width='100%', layout_orientation=gui.Container.LAYOUT_HORIZONTAL,
                                            margin='0px', style={'display': 'block', 'overflow': 'auto'})

        subContainerLeft = gui.Container(width=320,
                                         style={'display': 'block', 'overflow': 'auto', 'text-align': 'center'})
        self.img = gui.Image('/res:logo.png', height=100, margin='10px')

        # the arguments are	width - height - layoutOrientationOrizontal
        subContainerRight = gui.Container(
            style={'width': '220px', 'display': 'block', 'overflow': 'auto', 'text-align': 'center'})
        self.count = 0
        self.counter = gui.Label('', width=200, height=30, margin='10px')

        # label is used to state last command
        self.lbl = gui.Label('This is a LABEL!', width=200, height=30, margin='10px')

        self.bt = gui.Button('Press me!', width=200, height=30, margin='10px')
        # setting the listener for the onclick event of the button
        self.bt.onclick.do(self.on_button_pressed)

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

        self.slider = gui.Slider(10, 0, 100, 5, width=200, height=20, margin='10px')
        self.slider.onchange.do(self.slider_changed)

        self.date = gui.Date('2015-04-13', width=200, height=20, margin='10px')
        self.date.onchange.do(self.date_changed)

        # appending a widget to another, the first argument is a string key
        subContainerRight.append([self.counter, self.lbl, self.bt,
                                  self.move_out_10_btn, self.move_out_1_btn, self.move_in_1_btn, self.move_in_10_btn,
                                  self.move_up_10_btn, self.move_up_1_btn, self.move_down_1_btn, self.move_down_10_btn,
                                  self.zero_btn,
                                  self.take_measurement_btn, self.take_measurement_set_btn])
        # use a defined key as we replace this widget later

        subContainerRight.append([self.slider, self.date])
        self.subContainerRight = subContainerRight

        subContainerLeft.append([self.img])

        horizontalContainer.append([subContainerLeft, subContainerRight])

        verticalContainer.append([horizontalContainer])

        # this flag will be used to stop the display_counter Timer
        self.stop_flag = False

        # returning the root widget
        return verticalContainer

    # listener function
    def on_button_pressed(self, widget):
        self.lbl.set_text('Button pressed! ')
        self.bt.set_text('Hi!')

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

    def on_text_area_change(self, widget, newValue):
        self.lbl.set_text('Text Area value changed!')

    def on_spin_change(self, widget, newValue):
        self.lbl.set_text('SpinBox changed, new value: ' + str(newValue))

    def on_check_change(self, widget, newValue):
        self.lbl.set_text('CheckBox changed, new value: ' + str(newValue))

    def slider_changed(self, widget, value):
        self.lbl.set_text('New slider value: ' + str(value))

    def date_changed(self, widget, value):
        self.lbl.set_text('New date value: ' + value)

    def on_close(self):
        """ Overloading App.on_close event to stop the Timer.
        """
        self.stop_flag = True
        super(NFSApp, self).on_close()


if __name__ == "__main__":
    # starts the webserver
    # optional parameters
    # start(MyApp,address='127.0.0.1', port=8081, multiple_instance=False,enable_file_cache=True, update_interval=0.1, start_browser=True)
    start(NFSApp, debug=True, address='0.0.0.0', port=8083, start_browser=True, multiple_instance=True)