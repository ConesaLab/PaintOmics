//# sourceURL=UserController.js
/*
* (C) Copyright 2014 The Genomics of Gene Expression Lab, CIPF
* (http://bioinfo.cipf.es/aconesawp) and others.
*
* All rights reserved. This program and the accompanying materials
* are made available under the terms of the GNU Lesser General Public License
* (LGPL) version 3 which accompanies this distribution, and is available at
* http://www.gnu.org/licenses/lgpl.html
*
* This library is distributed in the hope that it will be useful,
* but WITHOUT ANY WARRANTY; without even the implied warranty of
* MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU
* Lesser General Public License for more details.
*
* Contributors:
*     Rafael Hernandez de Diego
*     rhernandez@cipf.es
*     Ana Conesa Cegarra
*     aconesa@cipf.es
*
* THIS FILE CONTAINS THE FOLLOWING COMPONENT DECLARATION
* - UserController
*
* EVENT HANDLERS MAPPING
*  - getUserViewsDialog
*  - signInLinkClickHandler
*  - signInButtonClickHandler
*  - signUpLinkClickHandler
*  - signUpButtonClickHandler
*  - signUpCloseButtonClickHandler
*  - forgotPassLinkClickHandler #Not yet implemented!
*  - signOutButtonClickHandler
*  - startGuestSessionButtonClickHandler
*  - showGuestSessionDialog
*  - myDataButtonClickHandler
*  - getCredentialsParams
*/
function UserController() {

    /**
    * This function returns a new dialog for user managemnet (login etc.)
    * @param {type} width
    * @param {type} height
    * @returns ExtJS.Dialog
    */
    this.getUserViewsDialog = function (width, height) {
        var userViewsDialog = Ext.getCmp("userViewsDialog");
        if (userViewsDialog == null) {
            userViewsDialog = Ext.create('Ext.window.Window', {
                id: "userViewsDialog",
                modal: true, closable: false,
                /* The window had no title and `closable: false`, so ExtJS drew
                   its header anyway: an empty 60px grey band across the top of
                   every account dialog, above content that names itself. The
                   design system's dialog has no chrome bar at all -- it is a
                   surface with a hairline, a shared corner radius and the title
                   inside the body -- so the header comes off rather than
                   acquiring a caption it does not need. */
                header: false,
                cls: "userViewsDialog",
                /* Only ever engages when the clamp below has had to shorten the
                   window: the sizes these forms ask for fit on a normal screen,
                   and a dialog that scrolls when it does not need to is worse
                   than one that does not. */
                autoScroll: true,
                items: []
            });
        }

        /* The window is centred and fixed-height, so anything taller than the
           viewport is not something the reader can scroll the page to reach -
           it is simply off the screen, top and bottom. Create an account is the
           form that gets near this: it asks for 700px, which is fine on a
           laptop and is not on a short window or a projector. Clamped, and
           autoScroll above then makes the remainder reachable. */
        var ceiling = Math.max(320, Ext.Element.getViewportHeight() - 40);
        userViewsDialog.setHeight(Math.min(height, ceiling));
        userViewsDialog.setWidth(width);
        userViewsDialog.center();
        return userViewsDialog;
    };

    /**
    * This function shows a new dialog for login
    */
    this.signInLinkClickHandler = function () {
        var userViewsDialog = this.getUserViewsDialog(760, 440);

        var signInPanel = new SignInPanel();
        signInPanel.setController(this);
        userViewsDialog.removeAll();
        userViewsDialog.add(signInPanel.getComponent());
        userViewsDialog.setLoading(false);
        /* Put the caret in the email box. ExtJS makes the window element itself
           focusable and focuses it on open, so the app's 3px --pa-focus-ring
           drew a blue rectangle around the whole dialog -- a focus indicator on
           something nobody can type into, while the field they can type into
           showed nothing. This has to hang off show()'s callback rather than
           the panel's afterrender: the panel renders when it is added, and the
           window then takes focus back as it opens, so focusing any earlier is
           immediately undone. */
        userViewsDialog.show(null, function () {
            var emailField = userViewsDialog.down('textfield[name=email]');
            if (emailField) {
                emailField.focus(false, 50);
            }
        });
    };

    /**
    * This function send to server the credentials for log in the application.
    *
    * @param {panel} userView
    */
    this.signInButtonClickHandler = function (userView) {
        var signInForm = userView.getComponent().queryById("signInForm");
        if (signInForm.isValid()) {
            $.ajax({
                type: "POST",
                url: SERVER_URL_UM_SIGNIN,
                data: signInForm.getForm().getValues(),
                success: function (response) {
                    if (response.success === false) {
                        $("#invalidUserPassMessage").html(response.message.split("ERROR MESSAGE:")[1]);
                        $("#invalidUserPassMessage").fadeIn();
                        /* The message is a plain div jQuery reveals, and the
                           panel around it was measured before it existed - see
                           poRelayout in UserViews.js. Without this the links
                           under the button are pushed out of a panel that
                           carries `overflow: hidden`. */
                        poRelayout(userView.getComponent());
                        return;
                    }
                    $("#invalidUserPassMessage").fadeOut();

                    /*1. Set the cookies*/
                    Ext.util.Cookies.set("lastEmail", signInForm.getForm().getValues().email, null, location.pathname);
                    Ext.util.Cookies.set("sessionToken", response.sessionToken, null, location.pathname);
                    Ext.util.Cookies.set("userID", response.userID, null, location.pathname);
                    Ext.util.Cookies.set("userName", response.userName, null, location.pathname);
                    Ext.util.Cookies.clear("nologin", location.pathname);
                    /*3. Close window*/
                    var userViewsDialog = Ext.getCmp("userViewsDialog");
                    if (userViewsDialog != null) {
                        userViewsDialog.close();
                    }

                    showSuccessMessage(
                        "Welcome back " + response.userName, {
                            message: (response.loginMessage !== null ? response.loginMessage.message_content: ""),
                            logMessage: "Signed in as " + response.userName,
                            callback: function () {
                                application.getController("JobController").resetButtonClickHandler(null, true);
                                location.reload();
                            }
                        });
                    },
                    error: ajaxErrorHandler
                });
            }
        };

        /**
        *
        * @param {type} userView
        * @returns {undefined}
        */
        this.signUpLinkClickHandler = function (userView) {
            var userViewsDialog = this.getUserViewsDialog(660, 700);

            userViewsDialog.setLoading(true);
            var signUpPanel = new SignUpPanel();
            signUpPanel.setController(this);
            userViewsDialog.removeAll();
            userViewsDialog.add(signUpPanel.getComponent());
            userViewsDialog.setLoading(false);
            userViewsDialog.show();
        };

        /**
        *
        * @param {type} userView
        * @returns {undefined}
        */
        this.signUpButtonClickHandler = function (userView) {
            var signUpForm = userView.getComponent().queryById("signUpForm");
            if (signUpForm.isValid()) {
                $.ajax({
                    type: "POST",
                    url: SERVER_URL_UM_SIGNUP,
                    data: signUpForm.getForm().getValues(),
                    success: function (response) {
                        if (response.success === false) {
                            $("#invalidSignUpMessage").html(response.message.split("ERROR MESSAGE:")[1]);
                            $("#invalidSignUpMessage").fadeIn();
                            return;
                        }
                        $("#invalidSignUpMessage").fadeOut();
                        /*1. Show Congratz panel*/
                        userView.showCongratzPanel();
                        Ext.util.Cookies.set("lastEmail", signUpForm.getForm().getValues().email, null, location.pathname);
                        //After that a confirmation email is sent and user must access to the provided link to enable account.
                        /*After confirmation, user must sign in app using the Sign In option*/
                    },
                    error: ajaxErrorHandler
                });
            }
        };

        /**
        *
        * @param {type} userView
        * @returns {undefined}
        */
        this.signUpCloseButtonClickHandler = function (userView) {
            var userViewsDialog = Ext.getCmp("userViewsDialog");
            if (userViewsDialog != null) {
                userViewsDialog.close();
            }
        };

        /**
        *
        * @param {type} userView
        * @returns {undefined}
        */
        this.forgotPassLinkClickHandler = function (userView) {
			var userViewsDialog = this.getUserViewsDialog(430, 345);

			var forgetPasswordPanel = new ForgetPasswordPanel();
			forgetPasswordPanel.setController(this);
			userViewsDialog.removeAll();
			userViewsDialog.add(forgetPasswordPanel.getComponent());
			userViewsDialog.setLoading(false);
			userViewsDialog.show();
        };
	
	
    /**
    * This function send to server the request to reset the password.
    *
    * @param {panel} userView
    */
    this.forgotPassButtonClickHandler = function (userView) {
        var signInForm = userView.getComponent().queryById("signInForm");
        if (signInForm.isValid()) {
            $.ajax({
                type: "GET",
                url: SERVER_URL_UM_RESETPASSWORD,
                data: signInForm.getForm().getValues(),
                success: function (response) {
                    if (response.success === false) {
                        $("#invalidEmailMessage").html(response.message.split("ERROR MESSAGE:")[1]);
                        /* Was #invalidUserPainvalidEmailMessagessMessage - the id of the
                           sign-in panel's message box with this panel's id spliced into
                           the middle of it by a search-and-replace. It matched nothing,
                           so the reveal never fired; the failure was invisible only
                           because the div carried two `style` attributes and the
                           `display:none` in the second one was discarded by the parser,
                           leaving it permanently shown. Both halves are fixed now, so
                           this selector has to be the real one. */
                        $("#invalidEmailMessage").fadeIn();
                        poRelayout(userView.getComponent());
                        return;
                    }
					
					$("#invalidEmailMessage").html('E-mail sent, please check your inbox for instructions.').css('color', 'green').fadeIn();
					/* Same re-measure as the failure branch above: the panel was laid
					   out before this line existed. */
					poRelayout(userView.getComponent());
                    },
                    error: ajaxErrorHandler
                });
            }
        };


        /**
        *
        * @param {type} userView
        * @returns {undefined}
        */
        this.changePassLinkClickHandler = function () {
            var userViewsDialog = this.getUserViewsDialog(440, 405);
            userViewsDialog.setLoading(true);
            var changePasswordPanel = new ChangePasswordPanel();
            changePasswordPanel.setController(this);
            userViewsDialog.removeAll();
            userViewsDialog.add(changePasswordPanel.getComponent());
            userViewsDialog.setLoading(false);
            userViewsDialog.show();
        };

        /**
        *
        * @param {type} userView
        * @returns {undefined}
        */
        this.changePasswordAcceptButtonClickHandler = function (userView) {
            var changePassForm = userView.getComponent().queryById("changePassForm");
            if (changePassForm.isValid()) {
                $.ajax({
                    type: "POST",
                    url: SERVER_URL_UM_CHANGEPASS,
                    data: changePassForm.getForm().getValues(),
                    success: function (response) {
                        /*1. Show Congratz panel*/
                        userView.showSuccessPanel();
                    },
                    error: ajaxErrorHandler
                });
            }
        };

        /**
        *
        * @param {type} userView
        * @returns {undefined}
        */
        this.changePasswordCancelButtonClickHandler = function (userView) {
            var userViewsDialog = Ext.getCmp("userViewsDialog");
            if (userViewsDialog != null) {
                userViewsDialog.close();
            }
        };

        /**
        *
        * @param {type} userView
        * @returns {undefined}
        */
        this.signOutButtonClickHandler = function (userView) {
            var loggedIn = Ext.util.Cookies.get("userID") !== null;
            if (Ext.util.Cookies.get("nologin") == null && loggedIn !== true) {
                var noLogin = true;
            } else {
                var noLogin = false;
            }


            if (noLogin != true) {
              $.ajax({
                  type: "POST",
                  url: SERVER_URL_UM_SIGNOUT,
                  data: this.getCredentialsParams(),
                  success: function (response) {
                      /*1. Clear the cookies*/
                      Ext.util.Cookies.clear("sessionToken", location.pathname);
                      Ext.util.Cookies.clear("userID", location.pathname);
                      Ext.util.Cookies.clear("userName", location.pathname);
                      Ext.util.Cookies.clear("nologin", location.pathname);
                      if (userView){
  											userView.getComponent().updateLoginState();
  										}
                      application.getController("JobController").resetButtonClickHandler(null, true, function() { location.reload(); });
                  },
                  error: ajaxErrorHandler
              });
            } else {
              application.getController("UserController").signInLinkClickHandler();
            }
        };

        /**
        *
        * @param {type} userView
        * @returns {undefined}
        */
        this.startGuestSessionButtonClickHandler = function (userView) {
            var me = this;

            $.ajax({
                type: "POST",
                url: SERVER_URL_UM_NEWGUESTSESSION,
                success: function (response) {
                    /*1. Set the cookies*/
                    Ext.util.Cookies.set("lastEmail", response.userName + "@" + PAINTOMICS_EMAIL_DOMAIN, null, location.pathname);
                    Ext.util.Cookies.set("sessionToken", response.sessionToken, null, location.pathname);
                    Ext.util.Cookies.set("userID", response.userID, null, location.pathname);
                    Ext.util.Cookies.set("userName", response.userName, null, location.pathname);
                    Ext.util.Cookies.clear("nologin", location.pathname);

                    /*2. Show Credentials dialog*/
                    me.showGuestSessionDialog(response.userName + "@" + PAINTOMICS_EMAIL_DOMAIN, response.p);
                },
                error: ajaxErrorHandler
            });
        };

        /**
        *
        * @param {type} userView
        * @returns {undefined}
        */
        this.startNoLoginSessionButtonClickHandler = function (userView) {
            var me = this;

            var loggedIn = Ext.util.Cookies.get("userID") !== null;
            if (Ext.util.Cookies.get("nologin") == null && loggedIn !== true) {
                var noLogin = true;
            } else {
                var noLogin = false;
            }


            /* If we are already in a "noLogin" session, close the dialog */
            if (noLogin == true) {
              this.signUpCloseButtonClickHandler();
            } else {
              $.ajax({
                  type: "POST",
                  url: SERVER_URL_UM_NEWNOLOGINSESSION,
                  success: function (response) {
                      /*1. Set the cookies*/
                      Ext.util.Cookies.clear("lastEmail", location.pathname);
                      Ext.util.Cookies.clear("sessionToken", location.pathname);
                      Ext.util.Cookies.clear("userID", location.pathname);
                      Ext.util.Cookies.clear("userName", location.pathname);

                      /* Assign the cookie that identifies the session as anonymous */
                      Ext.util.Cookies.set("nologin", true, null, location.pathname);

                      /*2. Show Credentials dialog*/
                      me.showNoLoginSessionDialog(null, response.p);
                  },
                  error: ajaxErrorHandler
              });
            }
        };

        /**
        *
        * @param {type} email
        * @param {type} p
        * @returns {undefined}
        */
        this.showGuestSessionDialog = function (email, p) {
            var userViewsDialog = this.getUserViewsDialog(700, 400);

            var guestSessionPanel = new GuestSessionPanel(email, p);
            guestSessionPanel.setController(this);
						guestSessionPanel.setParent(userViewsDialog);
            userViewsDialog.removeAll();
            userViewsDialog.add(guestSessionPanel.getComponent());
            userViewsDialog.setLoading(false);
            userViewsDialog.show();
        };

        /**
        *
        * @param {type} email
        * @param {type} p
        * @returns {undefined}
        */
        this.showNoLoginSessionDialog = function (email, p) {
            var userViewsDialog = this.getUserViewsDialog(700, 400);

            var noLoginSessionPanel = new NoLoginSessionPanel(email, p);
            noLoginSessionPanel.setController(this);
						noLoginSessionPanel.setParent(userViewsDialog);
            userViewsDialog.removeAll();
            userViewsDialog.add(noLoginSessionPanel.getComponent());
            userViewsDialog.setLoading(false);
            userViewsDialog.show();
        };

        /**
        *
        * @param {type} userView
        * @returns {undefined}
        */
        this.myDataButtonClickHandler = function (userView) {
            application.getController("DataManagementController").showMyDataPanelClickHandler(null);
        };

        /**
        *
        * @param {type} request_params
        * @returns {UserController.getCredentialsParams.credentials}
        */
        this.getCredentialsParams = function (request_params) {
            var credentials = {};
            if (request_params != null) {
                credentials = request_params;
            }

            credentials['sessionToken'] = Ext.util.Cookies.get('sessionToken');
            credentials['userID'] = Ext.util.Cookies.get('userID');
            return credentials;
        };
    }
