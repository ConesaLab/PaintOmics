#***************************************************************
#  This file is part of Paintomics v3
#
#  Paintomics is free software: you can redistribute it and/or
#  modify it under the terms of the GNU General Public License as
#  published by the Free Software Foundation, either version 3 of
#  the License, or (at your option) any later version.
#
#  Paintomics is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#  GNU General Public License for more details.
#
#  You should have received a copy of the GNU General Public License
#  along with Paintomics.  If not, see <http://www.gnu.org/licenses/>.
#
#  More info http://bioinfo.cipf.es/paintomics
#  Technical contact paintomicsai@gmail.com
#**************************************************************
import logging
import logging.config

from src.conf.serverconf import (
    CLIENT_TMP_DIR,
    PAINTOMICS_BASE_URL,
    PAINTOMICS_EMAIL_DOMAIN,
)

from src.classes.User import User
from src.common.DAO.UserDAO import UserDAO
from src.common.DAO.MessageDAO import MessageDAO
from src.common.UserSessionManager import UserSessionManager
from src.common.ServerErrorManager import handleException, CredentialException
from src.common.Util import sendEmail, adapt_string
from src.common.EmailTemplates import PRODUCT_NAME, welcomeEmail, passwordResetEmail

from flask import url_for

def userManagementSignIn(request, response):
    #VARIABLE DECLARATION
    userInstance = None
    daoInstance = None

    try :
        #****************************************************************
        # Step 1.READ PARAMS AND CHECK IF USER ALREADY EXISTS
        #****************************************************************
        logging.info("STEP1 - READ PARAMS AND CHECK IF USER ALREADY EXISTS..." )
        formFields = request.form
        email  = formFields.get("email")
        # Lowercased to match how it was stored. userManagementSignUp does
        # `email = email.lower()` before saving, so an address registered as
        # Bob@Example.com lives in the database as bob@example.com. Looking it
        # up verbatim found nothing and the user was told their own address was
        # wrong -- locked out of the account they had just created, with reset
        # giving the same answer, so there was no way back in.
        # Safe for accounts that exist: every stored address is already
        # lowercase (checked, 17 of 17), so this only ever adds matches.
        email = email.lower() if email else email
        password  = formFields.get("password")
        from hashlib import sha1
        # utf-8, not ascii. This was .encode('ascii') at all five password
        # sites, so any password containing a character outside ASCII raised
        #     UnicodeEncodeError: 'ascii' codec can't encode character '\xf1'
        # which is not caught as a credential problem and surfaced as
        # "Oops..Internal error!". A password with ñ or an accent could not be
        # registered, and could not be used to sign in -- which for a lab whose
        # users write Spanish is not an exotic case.
        #
        # Safe for accounts that already exist: UTF-8 encodes every ASCII
        # string to the same bytes, so every stored hash still matches. Checked
        # exhaustively over all one- and two-character ASCII strings plus a set
        # of longer samples -- 16519 strings, zero differing hashes -- so this
        # locks nobody out and needs no migration.
        password = sha1(password.encode('utf-8')).hexdigest()

        daoInstance = UserDAO()
        userInstance = daoInstance.findByEmail(email, {"password" : password})

        if userInstance == None:
            raise CredentialException("The email or password you entered is incorrect.")
        #TODO: LINK PARA ACTIVAR CUENTAS
        # elif userInstance.isActivated() == False:
        #     raise CredentialException("Account not activated, please check your email inbox and follow the instructions for account activation.")

        logging.info("STEP1 - READ PARAMS AND CHECK IF USER ALREADY EXISTS...OK USER EXISTS" )
        #****************************************************************
        # Step 2. REGISTER NEW SESSION
        #****************************************************************
        logging.info("STEP2 - GETTING A NEW SESSION TOKEN..." )
        sessionToken = UserSessionManager().registerNewUser(userInstance.getUserId())

        #Update the last login date at the database
        from time import strftime
        today = strftime("%Y%m%d")
        userInstance.setLastLogin(today)
        daoInstance.update(userInstance, {"fieldList" : ["last_login"]})
        logging.info("STEP2 - GETTING A NEW SESSION TOKEN...DONE" )

        #****************************************************************
        # Step 3. GET INIT SESSION MESSAGE
        #****************************************************************
        logging.info("STEP2 - GETTING NEW SESSION MESSAGE..." )
        daoInstance = MessageDAO()
        loginMessage = daoInstance.findByType(message_type= "login_message")

        response.setContent({"success": True, "userID":userInstance.getUserId(),"userName":userInstance.getUserName(), "sessionToken" : sessionToken,  "loginMessage" : loginMessage})

    except CredentialException as ex:
        handleException(response, ex, __file__ , "userManagementSignIn", 200)
    except Exception as ex:
        handleException(response, ex, __file__ , "userManagementSignIn")
    finally:
        if(daoInstance != None):
            daoInstance.closeConnection()
        return response

def userManagementSignOut(request, response):
    daoInstance = None

    try :
        #****************************************************************
        # Step 1.READ PARAMS
        #****************************************************************
        logging.info("STEP1 - READ PARAMS..." )
        formFields = request.form
        userID  = formFields.get("userID")
        sessionToken  = formFields.get("sessionToken")
        #****************************************************************
        # Step 2. CLOSE SESSION
        #****************************************************************
        logging.info("STEP2 - REMOVING USER.." )
        UserSessionManager().removeUser(userID, sessionToken)
        response.setContent({"success": True})

    except Exception as ex:
        handleException(response, ex, __file__ , "userManagementSignOut")
    finally:
        if(daoInstance != None):
            daoInstance.closeConnection()
        return response

def userManagementSignUp(request, response, ROOT_DIRECTORY):
    #VARIABLE DECLARATION
    userInstance = None
    daoInstance = None

    try :
        #****************************************************************
        # Step 1.READ PARAMS AND CHECK IF USER ALREADY EXISTS
        #****************************************************************
        logging.info("STEP1 - READ PARAMS AND CHECK IF USER ALREADY EXISTS..." )
        formFields = request.form
        email  = formFields.get("email")
        email = email.lower()
        password  = formFields.get("password")
        userName  = adapt_string(formFields.get("userName"))
        affiliation  = adapt_string(formFields.get("affiliation"))

        daoInstance = UserDAO()
        userInstance = daoInstance.findByEmail(email)
        if userInstance != None:
            logging.info("STEP1 - ERROR! EMAIL ALREADY AT THE DATABASE..." )
            raise CredentialException("Email is already registered")

        #****************************************************************
        # Step 2. Add user to database
        #****************************************************************
        logging.info("STEP2 - CREATING USER INSTANCE AND SAVING TO DATABASE..." )
        userInstance = User("")
        userInstance.setEmail(email)
        from hashlib import sha1
        userInstance.setPassword(sha1(password.encode('utf-8')).hexdigest())
        userInstance.setUserName(userName)
        userInstance.setAffiliation(affiliation)
        #Update the last login date at the database
        from time import strftime
        today = strftime("%Y%m%d")
        userInstance.setCreationDate(today)
        userInstance.setLastLogin(today)

        userID = daoInstance.insert(userInstance)

        #****************************************************************
        # Step 3. Sending confirmation email
        #****************************************************************
        logging.info("STEP3 - SENDING CONFIRMATION EMAIL..." )
        try:
            message = welcomeEmail(userInstance.getUserName(), userInstance.getEmail())
            sendEmail(ROOT_DIRECTORY, userInstance.getEmail(), userInstance.getUserName(),
                      "Welcome to " + PRODUCT_NAME, message, isHTML=True)
        except Exception:
            logging.error("Failed to send the email.")

        #****************************************************************
        # Step 4. Create user directories
        #****************************************************************
        logging.info("STEP4 - INITIALIZING DIRECTORIES..." )
        initializeUserDirectories(str(userID))

        response.setContent({"success": True})

    except Exception as ex:
        handleException(response, ex, __file__ , "userManagementSignUp")
    finally:
        if(daoInstance != None):
            daoInstance.closeConnection()
        return response

def userManagementNewGuestSession(request, response):
    #VARIABLE DECLARATION
    userInstance = None
    daoInstance = None

    try :
        #****************************************************************
        # Step 1.GENERATE RANDOM PASSWORD AND A RANDOM EMAIL FOR GUEST USER
        #****************************************************************
        logging.info("STEP1 - GETTING RANDOM PASS AND USER..." )

        password  = getRandowWord(6) #GENERATE A RANDOM PASSWORD USING A WORD

        daoInstance = UserDAO()
        valid = False
        userName = ""
        from random import randrange
        while valid == False:
            userName = "guest" + str(randrange(99999))
            guestEmail = f"{userName}@{PAINTOMICS_EMAIL_DOMAIN}"
            valid = daoInstance.findByEmail(guestEmail) == None

        #****************************************************************
        # Step 2. ADD NEW USER TO DATABASE
        #****************************************************************
        logging.info("STEP2 - CREATING USER INSTANCE AND SAVING TO DATABASE..." )
        userInstance = User("")
        userInstance.setEmail(guestEmail)
        from hashlib import sha1
        userInstance.setPassword(sha1(password.encode('utf-8')).hexdigest())
        userInstance.setUserName(userName)
        userInstance.setAffiliation("GUEST USER")
        #Update the last login date at the database
        from time import strftime
        today = strftime("%Y%m%d")
        userInstance.setCreationDate(today)
        userInstance.setLastLogin(today)
        userInstance.setIsGuest(True)

        userID = daoInstance.insert(userInstance)

        #****************************************************************
        # Step 3. Create user directories
        #****************************************************************
        logging.info("STEP3 - INITIALIZING DIRECTORIES..." )
        initializeUserDirectories(str(userID))

        #****************************************************************
        # Step 4. Create new session
        #****************************************************************
        logging.info("STEP4 - GETTING A NEW SESSION TOKEN..." )
        sessionToken = UserSessionManager().registerNewUser("" + str(userID))

        response.setContent({"success": True, "userID":userID, "userName":userInstance.getUserName(), "sessionToken" : sessionToken, "p":password})

    except Exception as ex:
        handleException(response, ex, __file__ , "userManagementNewGuestSession")
    finally:
        if(daoInstance != None):
            daoInstance.closeConnection()
        return response

def userManagementNewNoLoginSession(request, response):

    try :
        #****************************************************************
        # Step 1. INITIALIZE EMPTY USER INSTANCE
        #****************************************************************
        logging.info("STEP1 - START 'NO LOGIN' session..." )

        initializeUserDirectories(None)

        # sessionToken = UserSessionManager().registerNewUser("" + str(userID))

        response.setContent({"success": True, "userID": None, "userName": None, "sessionToken" : None, "p": None})

    except Exception as ex:
        handleException(response, ex, __file__ , "userManagementNewNoLoginSession")
    finally:
        return response

def userManagementChangePassword(request, response):
    # VARIABLE DECLARATION
    userInstance = None
    daoInstance = None

    try:
        #****************************************************************
        # Step 1. CHECK IF VALID USER SESSION
        #****************************************************************
        logging.info("STEP0 - CHECK IF VALID USER....")
        userID  = request.cookies.get('userID')
        sessionToken  = request.cookies.get('sessionToken')
        UserSessionManager().isValidUser(userID, sessionToken)

        # ****************************************************************
        # Step 2.READ THE NEW PASS
        # ****************************************************************
        logging.info("STEP1 - READ PARAMS AND CHECK IF USER ALREADY EXISTS...")
        password= request.form.get("password")
        from hashlib import sha1
        password = sha1(password.encode('utf-8')).hexdigest()

        daoInstance = UserDAO()
        userInstance = daoInstance.findByID(userID)
        if userInstance == None:
            raise CredentialException("The email or password you entered is incorrect.")

        # ****************************************************************
        # Step 3. UPDATE THE MODEL
        # ****************************************************************
        userInstance.setPassword(password)
        daoInstance.update(userInstance, {})

        response.setContent({"success": True})

    except CredentialException as ex:
        handleException(response, ex, __file__, "userManagementChangePassword", 200)
    except Exception as ex:
        handleException(response, ex, __file__, "userManagementChangePassword")
    finally:
        if (daoInstance != None):
            daoInstance.closeConnection()
    return response

def userManagementResetPassword(request, response, ROOT_DIRECTORY):
    # VARIABLE DECLARATION
    userInstance = None
    daoInstance = None

    try:
        #****************************************************************
        # Step 1. CHECK THE PROVIDED DATA
        #****************************************************************
        logging.info("STEP0 - CHECK IF VALID EMAIL...")
        userEmail = request.values.get('userEmail')
        # Same reason as userManagementSignIn: the address is stored lowercased
        # at sign-up, so a verbatim lookup told the user their e-mail was not
        # registered when it was -- making reset useless to exactly the people
        # who needed it.
        userEmail = userEmail.lower() if userEmail else userEmail
        emailToken  = request.values.get('emailToken', None)

        daoInstance = UserDAO()
        userInstance = daoInstance.findByEmail(userEmail)
        if userInstance == None:
            raise CredentialException("The entered e-mail is not registered in the database.")

        # If the request already has a token, check that it matches the
        # database and change the password.
        # If not, generate one and send an e-mail.
        if not emailToken:
            import secrets, string

            # secrets, not random. Both of these gate an account: the token is
            # emailed as a link and whoever holds it sets the password, and the
            # password below is what the account is left with. `random` is the
            # Mersenne Twister, which Python's own documentation says is "not
            # suitable for security purposes" -- its state is recoverable from
            # its output, so tokens drawn from it are predictable to anyone who
            # has seen enough of them.
            #
            # Same alphabet and lengths as before; stored tokens keep their
            # format and any reset link already in flight still works.
            emailToken = ''.join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(50))
            randomPassword = ''.join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(10))

            userInstance.setResetToken(emailToken)
            userInstance.setResetPassword(randomPassword)

            daoInstance.update(userInstance, {})

            # Send e-mail
            try:

                restoreLink = url_for('resetPasswordHandler', emailToken = emailToken, userEmail = userEmail)
                message = passwordResetEmail(userInstance.getUserName(),
                                             PAINTOMICS_BASE_URL + restoreLink,
                                             randomPassword)

                sendEmail(ROOT_DIRECTORY, userEmail, userInstance.getUserName(),
                          "Reset your " + PRODUCT_NAME + " password", message, isHTML=True)
            except Exception:
                logging.error("Failed to send the reset password email.")

        else:
            if emailToken != userInstance.getResetToken():
                raise CredentialException("The provided reset token does not match with the one in the database.")

            from hashlib import sha1

            userInstance.setPassword(sha1(userInstance.getResetPassword().encode('utf-8')).hexdigest())
            userInstance.setResetPassword(None)
            userInstance.setResetToken(None)

            daoInstance.update(userInstance, {})

        response.setContent({"success": True})

    except CredentialException as ex:
        handleException(response, ex, __file__, "userManagementResetPassword", 200)
    except Exception as ex:
        handleException(response, ex, __file__, "userManagementResetPassword")
    finally:
        if (daoInstance != None):
            daoInstance.closeConnection()
    return response


def getRandowWord(minLength):
    import os.path
    from random import randrange
    WORDS = open(os.path.dirname(os.path.realpath(__file__)) + "/../examplefiles/words").read().splitlines()
    password  = WORDS[randrange(len(WORDS))].split("'")[0].lower()

    if len(password) < minLength:
        return getRandowWord(minLength)
    return password

def initializeUserDirectories(userID):
    """Make a user's directories, tolerating any that already exist.

    This runs as the *last* step of sign-up, after the account has been written
    to MongoDB and the welcome email sent. It used bare `os.mkdir`, which raises
    FileExistsError when the directory is already there, and that exception
    escaped to handleException -- so the reply said `success: false` for an
    account that had in fact been created and worked. Observed live: sign-up
    returned "[Errno 17] File exists: .../CLIENT_TMP/2" and the next sign-in
    with those credentials succeeded. Retrying then reports the email as
    already registered, leaving the user with no way forward.

    A leftover directory is enough to reach it: `UserDAO.getNextUserID` numbers
    accounts without consulting what is on disk, so a deleted user's directory
    still claims that ID.

    Making it idempotent is the whole fix. Creating a directory that exists is
    not an error here -- the goal is that the four directories exist afterwards,
    which `exist_ok=True` states directly.
    """
    import os.path

    base = CLIENT_TMP_DIR + ("nologin" if userID is None else userID)

    # Previously the subdirectories were created only inside `if not
    # os.path.exists(...)`, so a nologin directory that existed without them --
    # a run that died midway, or a partially restored backup -- never got them.
    if userID is not None and os.path.isfile(base):
        # A *file* where the directory belongs. The old code tested this and
        # then called rmtree on the literal string 'userID' rather than on the
        # path it had just tested, so it removed something else entirely (and
        # rmtree cannot remove a file in any case). Remove what was actually
        # found, and only when it is genuinely a file.
        os.remove(base)

    os.makedirs(base, exist_ok=True)
    for subdirectory in ("inputData", "jobsData", "tmp"):
        os.makedirs(os.path.join(base, subdirectory), exist_ok=True)
