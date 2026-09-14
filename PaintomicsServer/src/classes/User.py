#***************************************************************
#  This file is part of Paintomics v3
#
#  Paintomics is free software: you can redistribute it and/or
#  modify it under the terms of the GNU General Public License as
#  published by the Free Software Foundation, either version 3 of
#  the License, or (at your option) any later version.
#
#  Paintomics is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#  GNU General Public License for more details.
#
#  You should have received a copy of the GNU General Public License
#  along with Paintomics.  If not, see <http://www.gnu.org/licenses/>.
#
#  More info http://bioinfo.cipf.es/paintomics
#  Technical contact paintomicsai@gmail.com
#**************************************************************

from src.common.Util import Model

class User (Model):
    #******************************************************************************************************************
    # CONSTRUCTORS
    #******************************************************************************************************************
    def __init__(self, userID):
        self.userID = userID
        self.sessionToken = ""
        self.userName = ""
        self.email = ""
        self.password = ""
        self.affiliation = ""
        self.activated= False
        self.creation_date = ""
        self.last_login = ""
        self.is_guest = False
        self.resetToken = None
        self.resetPassword = None

    #******************************************************************************************************************
    # GETTERS AND SETTER
    #******************************************************************************************************************
    def getUserId(self):
        return self.userID

    def setSessionToken(self, sessionToken):
        self.sessionToken = sessionToken

    def getUserName(self):
        return self.userName

    def setUserName(self, userName):
        self.userName = userName

    def getEmail(self):
        return self.email

    def setEmail(self, email):
        # Stored lowercased, because every lookup is now lowercased.
        # userManagementSignIn and userManagementResetPassword lower the address
        # before searching, so anything written with a capital becomes
        # unreachable -- demonstrated by planting zzGuestCase@Example.COM and
        # failing to sign in with that exact string.
        #
        # Sign-up already lowered its own copy; guest creation did not. It
        # builds "guest<n>@" + PAINTOMICS_EMAIL_DOMAIN, and that domain comes
        # from an environment variable, so a deployment setting it as
        # Paintomics.CSIC.es would have produced guest accounts that could never
        # log in. It is lowercase here, which is why nothing is broken today.
        #
        # Normalising in the setter rather than at each call site means the
        # stored form and the looked-up form cannot drift apart again, whatever
        # a future caller does.
        self.email = email.lower() if isinstance(email, str) else email

    def setPassword(self, password):
        self.password = password

    def getAffiliation(self):
        return self.affiliation

    def setAffiliation(self, affiliation):
        self.affiliation= affiliation

    def setCreationDate(self, creation_date):
        self.creation_date= creation_date

    def setLastLogin(self, last_login):
        self.last_login= last_login

    def isGuest(self):
        return self.is_guest

    def setIsGuest(self, is_guest):
        self.is_guest = is_guest

    def getResetToken(self):
        return self.resetToken

    def setResetToken(self, token):
        self.resetToken = token

    def getResetPassword(self):
        return self.resetPassword

    def setResetPassword(self, password):
        self.resetPassword = password
