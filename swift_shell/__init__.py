# Copyright 2018 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from ipykernel.zmqshell import ZMQInteractiveShell
from jupyter_client.session import Session


class CapturingSocket:
    """Simulates a ZMQ socket, saving messages instead of sending them.

    We use this to capture display messages.
    """

    def __init__(self):
        self.messages = []

    def send_multipart(self, msg, **kwargs):
        self.messages.append(msg)


class SwiftShell(ZMQInteractiveShell):
    """An IPython shell, modified to work within Swift."""

    def enable_gui(self, gui):
        """Disable the superclass's `enable_gui`.

        `enable_matplotlib("inline")` calls this method, and the superclass's
        method fails because it looks for a kernel that doesn't exist. I don't
        know what this method is supposed to do, but everything seems to work
        after I disable it.
        """
        pass


def create_shell(username, session_id, key):
    """Instantiates a CapturingSocket and SwiftShell and hooks them up.
    
    After you call this, the returned CapturingSocket should capture all
    IPython display messages.
    """
    socket = CapturingSocket()
    session = Session(username=username, session=session_id, key=key)
    shell = SwiftShell.instance()
    shell.display_pub.session = session
    shell.display_pub.pub_socket = socket
    return (socket, shell)
