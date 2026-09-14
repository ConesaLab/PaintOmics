//# sourceURL=UserViews.js
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
 * - SessionInfoBar
 * - SignInPanel
 * - SignUpPanel
 *
 */

/* Shared furniture for the account dialogs -----------------------------------
 *
 * Sign in, Create an account, Reset password and Change password render into
 * one window, and each used to open with a bare `<h2>` and nothing else: no
 * indication of which product the credentials belong to, and no sentence
 * saying what the form is for. These three build the pieces they now share, in
 * one place, so the four panels cannot drift apart again.
 */

/**
 * The lockup at the top of an account form: product mark, title, and one line
 * saying what the form does.
 *
 * @param {String} title      Heading text. Already-safe literal, not user data.
 * @param {String} subtitle   Optional supporting line; omitted if empty.
 * @returns {String} HTML
 */
function poAuthHeader(title, subtitle) {
    return '<div class="po-auth-head">' +
            '<div class="po-auth-brand">' +
            '  <img src="resources/images/paintomics-mark.svg" alt="" aria-hidden="true">' +
            '  <span>PaintOmics AI</span>' +
            '</div>' +
            '<h2>' + title + '</h2>' +
            (subtitle ? '<p class="po-auth-sub">' + subtitle + '</p>' : '') +
            '</div>';
}

/**
 * The confirmation state of a form that has been submitted - "Check your
 * inbox", "Password changed". A result, not a form, so it leads with a glyph
 * rather than with the product lockup.
 *
 * @param {String} icon    Font Awesome class, e.g. 'fa-envelope-o'.
 * @param {String} title   Heading text.
 * @param {String} body    Optional supporting line.
 * @returns {String} HTML
 */
function poAuthNotice(icon, title, body) {
    return '<div class="po-auth-notice">' +
            '<span class="po-auth-notice-icon"><i class="fa ' + icon + '" aria-hidden="true"></i></span>' +
            '<h2>' + title + '</h2>' +
            (body ? '<p class="po-auth-sub">' + body + '</p>' : '') +
            '</div>';
}

/**
 * Resizes the one account window to fit a panel that has just replaced a
 * bigger one.
 *
 * The window is created at the size of the form it opened with and then swaps
 * its body for a confirmation - three lines and a Close button - without
 * changing size, so the result sits in the top fifth of an otherwise empty
 * dialog. Centred again afterwards, or it stays pinned by its old top-left.
 *
 * @param {Number} width
 * @param {Number} height
 */
function poResizeDialog(width, height) {
    var dialog = Ext.getCmp("userViewsDialog");
    if (dialog === null || dialog === undefined) {
        return;
    }
    dialog.setWidth(width);
    dialog.setHeight(height);
    dialog.center();
}

/**
 * Re-measures a laid-out account panel after something outside ExtJS has
 * changed how tall its contents are.
 *
 * The validation lines in these forms are plain divs that jQuery reveals, and
 * an ExtJS panel body is sized once at layout time and then given
 * `overflow: hidden`. So a revealed message pushes whatever sits under it -
 * in practice the "Back" link - out of the panel and out of sight. Asking the
 * component to lay out again is what puts the two back in step.
 *
 * @param {Ext.Component} cmp  The panel that owns the message.
 */
function poRelayout(cmp) {
    if (cmp && cmp.updateLayout) {
        cmp.updateLayout();
    }
}

/**
 * Adds a show/hide control to every password field inside a rendered
 * component.
 *
 * Done against the DOM rather than with an ExtJS trigger field: this is
 * ExtJS 4.2, where turning a `textfield` into a `triggerfield` means a
 * different xtype and a different validation path for every one of the five
 * password inputs in this file. Flipping the `type` attribute does not touch
 * the ExtJS field object, so `getValues()`, `isValid()` and the
 * password-match validator all keep reading the same value they did before.
 *
 * Idempotent: afterrender can fire more than once for a component that is
 * removed from the window and added back, and a second button would stack on
 * top of the first.
 *
 * @param {Ext.Component} cmp  A rendered component to search within.
 */
function poAttachPasswordReveal(cmp) {
    var root = (cmp && cmp.getEl) ? cmp.getEl().dom : null;
    if (root === null || root === undefined) {
        return;
    }
    $(root).find('input[type=password]').each(function () {
        var input = $(this);
        /* `.x-form-item-body` is the cell ExtJS 4 wraps an input in; it is what
           the toggle is positioned against. Without it there is nothing to
           anchor to, so leave the field alone rather than dropping a button
           into an unknown box. */
        var wrap = input.closest('.x-form-item-body');
        if (wrap.length === 0 || wrap.hasClass('po-pass-wrap')) {
            return;
        }
        wrap.addClass('po-pass-wrap');

        var button = $('<button type="button" class="po-pass-toggle" ' +
                'aria-label="Show password" aria-pressed="false">' +
                '<i class="fa fa-eye" aria-hidden="true"></i></button>');

        button.on('click', function (event) {
            event.preventDefault();
            var revealed = input.attr('type') === 'text';
            input.attr('type', revealed ? 'password' : 'text');
            button.attr('aria-pressed', revealed ? 'false' : 'true');
            button.attr('aria-label', revealed ? 'Show password' : 'Hide password');
            button.find('i').attr('class', revealed ? 'fa fa-eye' : 'fa fa-eye-slash');
        });

        wrap.append(button);
    });
}

function SessionInfoBar() {
    /*********************************************************************
     * ATTRIBUTES
     ***********************************************************************/
    this.name = "SessionInfoBar";
    /*********************************************************************
     * OTHER FUNCTIONS
     ***********************************************************************/
    this.signInButtonClick = function () {
        this.getController().signInLinkClickHandler(this);
    };
    this.myDataButtonClick = function () {
        this.getController().myDataButtonClickHandler(this);
    };
    this.signOutButtonClick = function () {
        this.getController().signOutButtonClickHandler(this);
    };
    /*********************************************************************
     * OTHER FUNCTIONS
     ***********************************************************************/
    this.initComponent = function () {
        var me = this;
        this.component = Ext.widget(
                {xtype: "container", id: 'sessionInfoBar',
                    width: 150, height: 40, layout: 'hbox', style: {marginTop: "40px"},
                    items: [
                        // anonymous_user_30x30.png is in neither the repository nor the
                        // deployed image. The glyph needs no file and matches how the
                        // rest of the chrome draws its icons.
                        //
                        // This does not reach a screen today: MainView builds a
                        // SessionInfoBar and never adds it to a container - see the TODO
                        // above that call. So the image was a latent break, not a visible
                        // one, and it is fixed here so that wiring the widget up does not
                        // also mean chasing a missing file.
                        {xtype: "box", height: 40, width: 30, html: '<i class="fa fa-user sessionAvatar" aria-hidden="true"></i>'},
                        {xtype: "button", itemId: "buttonSessionOptions", text: "Not logged in!", height: 30,
                            style: {background: "none", border: "none"},
                            menu: {
                                xtype: 'menu',
                                items: [
                                    {xtype: 'menuitem', scale: 'small', itemId: "signInButton", handler: this.signInButtonClick, scope: this, cls: "notLoggedButtons", iconCls: 'login', text: 'Sign in'},
//                                    {xtype: 'menuitem', scale: 'small', itemId: "recoverJobButton", handler: this.recoverJobButtonClick, scope: this, text: 'Recover a job'},
//                                    {xtype: 'menuitem', scale: 'small', itemId: "myDataButton", handler: this.myDataButtonClick, scope: this, cls: "loggedButtons", hidden: true, text: 'My data'},
                                    {xtype: 'menuitem', scale: 'small', itemId: "signOutButton", handler: this.signOutButtonClick, scope: this, cls: "loggedButtons", hidden: true, text: 'Sign out'}
                                ]
                            }
                        }
                    ],
                    updateLoginState: function () {
                        var loggedIn = Ext.util.Cookies.get("userID") !== null;
                        var text = (loggedIn == true) ? Ext.util.Cookies.get("userName") : "Please Sign In";
                        this.queryById('buttonSessionOptions').setText(text);
                        this.queryById('signInButton').setVisible(loggedIn !== true);
                        this.queryById('signOutButton').setVisible(loggedIn === true);
                        $(".loggedOption").css("display", (loggedIn == true) ? "block" : "none");
                    }
                }
        );
    };
    return this;
}
SessionInfoBar.prototype = new View;

function SignInPanel() {
    /*********************************************************************
     * ATTRIBUTES
     ***********************************************************************/
    this.name = "SignInPanel";
    /*********************************************************************
     * OTHER FUNCTIONS
     ***********************************************************************/
    this.signInButtonClick = function () {
        this.getController().signInButtonClickHandler(this);
    };
    this.signUpLinkClick = function () {
        this.getController().signUpLinkClickHandler(this);
    };
    this.forgotPassLinkClick = function () {
        this.getController().forgotPassLinkClickHandler(this);
    };
    this.startGuestSessionButtonClick = function () {
        this.getController().startGuestSessionButtonClickHandler(this);
    };
    this.startNoLoginButtonClick = function () {
        this.getController().startNoLoginSessionButtonClickHandler(this);
    };

    this.initComponent = function () {
        var me = this;
        this.component = Ext.widget(
                {xtype: "container", cls: "po-auth", layout: {type: 'vbox', align: 'stretch'}, flex: 1,
                  items: [
                  {xtype: "container", cls: "po-auth-cols", layout: {type: 'hbox', align: 'stretch'}, flex: 1, maxWidth: 900, margin: '20px',
                      items: [
                          {xtype: 'form', itemId: "signInForm", flex: 1, border: 0, cls: "po-auth-form",
                              layout: {type: 'vbox', align: 'stretch'}, defaults: {labelAlign: "top", labelSeparator: "", border: false},
                              items: [
                                  {xtype: "box", html: poAuthHeader('Sign in', 'Your files and jobs stay in your account between sessions.')},
                                  /* validateOnBlur off on both. The dialog now puts the caret in
                                     the email box when it opens, and ExtJS validates a field the
                                     moment it loses focus - so clicking anywhere else, or tabbing
                                     to the password box, painted a red invalid border on an empty
                                     field nobody had tried to fill in yet. Telling someone they
                                     are wrong before they have typed is not validation, it is
                                     nagging. The form is still checked on submit:
                                     signInButtonClickHandler gates on signInForm.isValid(). */
                                  {xtype: "textfield", name: 'email', fieldLabel: 'Email address', vtype: 'email', emptyText: 'you@institution.org', value: Ext.util.Cookies.get('lastEmail'), allowBlank: false, validateOnBlur: false},
                                  {xtype: "textfield", name: 'password', fieldLabel: 'Password', inputType: 'password', allowBlank: false, validateOnBlur: false,
                                      listeners: {
                                          specialkey: function (field, e) {
                                              if (e.getKey() === e.ENTER) {
                                                  me.signInButtonClick();
                                              }
                                          }
                                      }},
                                  {xtype: "box", html:
                                              '<div class="formMessage" id="invalidUserPassMessage"></div>' +
                                              '<p data-guides="ignore" class="formActionRow"><a class="button btn-primary btn-form-action" href="javascript:void(0)" id="signInLink"><i class="fa fa-sign-in" aria-hidden="true"></i> Sign in</a></p>' +
                                              '<p class="po-auth-links"><a id="forgotPassLink" href="javascript:void(0)">Forgot your password?</a></p>' +
                                              '<p class="po-auth-alt">New to PaintOmics? <a class="signUpLink" href="javascript:void(0)">Create an account</a></p>'
                                  }
                              ]
                          },
						  	{xtype: "box", flex: 1, cls: "po-auth-aside", html:
                              '<div class="signInColumnDivider">' +
                              '  <h2>No account</h2>' +
                              '  <p>Your job is reachable only from the URL PaintOmics gives you when it starts, so save that URL. Without it the job cannot be recovered.</p>' +
                              '  <p data-guides="ignore" class="formActionRow"><a class="button btn-default btn-form-action" href="javascript:void(0)" id="noLoginButton"><i class="fa fa-arrow-right" aria-hidden="true"></i> Continue without an account</a></p>' +
                              '</div>'
                  			}
                      ]
                  }
              ],
              listeners: {
                  afterrender: function (cmp) {
                      poAttachPasswordReveal(cmp);
                      $("#forgotPassLink").click(function () {
                          me.forgotPassLinkClick();
                      });
                      $(".signUpLink").click(function () {
                          me.signUpLinkClick();
                      });
                      $("#signInLink").click(function () {
                          me.signInButtonClick();
                      });
                      $("#guestUserButton").click(function () {
                          me.startGuestSessionButtonClick();
                      });
                      $("#noLoginButton").click(function () {
                          me.startNoLoginButtonClick();
                      });
                  }
              }
            }
        );
        return this.component;
    };
    return this;
}
SignInPanel.prototype = new View;

function SignUpPanel() {
    /*********************************************************************
     * ATTRIBUTES
     ***********************************************************************/
    this.name = "SignUpPanel";
    /*********************************************************************
     * OTHER FUNCTIONS
     ***********************************************************************/
    this.signUpButtonClick = function () {

		if (! Ext.getCmp("conditionsCheckbox").checked) {
			$('#invalidSignUpMessage').html('You must accept the conditions to register.').fadeIn();
		} else {
			$('#invalidSignUpMessage').fadeOut();
		}
		/* The line above changes how tall the form is, and ExtJS does not know
		   it: the panel was measured once when it was laid out, and its body
		   carries `overflow: hidden` at that height. Revealing the message
		   pushed the form 18px taller, so the "Back to sign in" link under the
		   button fell outside the panel and was clipped - the reader is told
		   they have made a mistake and loses the way back at the same moment.
		   Re-measuring here is what keeps the two in step. */
		poRelayout(this.getComponent());

        this.getController().signUpButtonClickHandler(this);
    };
    this.signUpCloseButtonClick = function () {
        this.getController().signInLinkClickHandler(this);
    };
    this.signUpBackLinkClick = function () {
        this.getController().signInLinkClickHandler();
    };

    this.showCongratzPanel = function () {
        var signUpForm = this.getComponent().queryById("signUpForm");
        var congratzPanel = this.getComponent().queryById("congratzPanel");

        var userName = signUpForm.down("textfield[name=userName]").getValue();
        var email = signUpForm.down("textfield[name=email]").getValue();

        /* htmlEncode because this is a value the reader typed a moment ago and
           it is being put back into the page as markup. The class replaces an
           inline `font-size: 20px`, which no stylesheet - dark.css included -
           could reach. */
        var tpl = new Ext.Template("<p class='po-auth-notice-body'>A confirmation email was sent to {1}. Follow the instructions in it to activate your account.</p>");
        tpl = tpl.apply([Ext.String.htmlEncode(userName), Ext.String.htmlEncode(email)]);

        congratzPanel.queryById("messageBox").update(tpl);

        signUpForm.setVisible(false);
        congratzPanel.setVisible(true);
        /* The window was sized for a six-field form and this is three lines and
           a button, so without this the confirmation sits in the top fifth of
           700px of empty dialog. */
        poResizeDialog(420, 300);
    };

    this.initComponent = function () {
        var me = this;
        this.component = Ext.widget(
                {xtype: "container", cls: "po-auth po-auth-signup", layout: {type: 'vbox', align: 'stretch'}, flex: 1, maxWidth: 900, margin: '20',
                    items: [
                        {xtype: 'form', itemId: "signUpForm", border: 0, cls: "po-auth-form", layout: {type: 'vbox', align: 'stretch'}, defaults: {labelAlign: "top", labelSeparator: "", border: false},
                            items: [
                                {xtype: "box", html: poAuthHeader('Create an account', 'An account keeps your files and results past the seven days an unregistered job gets.')},
                                /* Paired across two columns rather than stacked in one.
                                   Five full-width inputs plus a scrolling notice and a
                                   consent tick made a form 850px tall, which does not fit
                                   on a laptop screen - the dialog is fixed-height and
                                   centred, so the overflow is not something you can scroll
                                   to. The pairs are the ones that belong together anyway:
                                   who you are, then the password and its confirmation.

                                   Ext.form.Basic collects fields from the whole descendant
                                   tree, so nesting them in an hbox does not change what
                                   getValues() returns. The gap is stated as an ExtJS
                                   `margin` and not in CSS because the hbox measures its
                                   items' margins when it divides the width. */
                                {xtype: "container", cls: "po-auth-row", layout: {type: 'hbox', align: 'stretch'},
                                    defaults: {labelAlign: "top", labelSeparator: "", border: false, flex: 1},
                                    items: [
                                        {xtype: "textfield", name: 'email', fieldLabel: 'Email address', vtype: 'email', emptyText: 'you@institution.org', allowBlank: false, margin: '0 9 0 0'},
                                        {xtype: "textfield", name: 'userName', fieldLabel: 'Your name or nickname', allowBlank: false, margin: '0 0 0 9'}
                                    ]
                                },
                                {xtype: "container", cls: "po-auth-row", layout: {type: 'hbox', align: 'stretch'},
                                    defaults: {labelAlign: "top", labelSeparator: "", border: false, flex: 1},
                                    items: [
                                        {xtype: "textfield", name: 'password', fieldLabel: 'Choose a password', inputType: 'password', allowBlank: false, margin: '0 9 0 0'},
                                        {xtype: "textfield", name: 'password2', fieldLabel: 'Confirm password', inputType: 'password', submitValue: false, allowBlank: false, margin: '0 0 0 9',
                                            validator: function (value) {
                                                if ($("input[name=password]").val() != value) {
                                                    return "Passwords do not match!";
                                                }
                                                return true;
                                            }
                                        }
                                    ]
                                },
                                {xtype: "textfield", name: 'affiliation', fieldLabel: 'Your affiliation (optional)'},
                                {xtype: "box", html: '<p class="formNode po-auth-hint">Please let us know your university, research centre or company and the department or institute.</p>'},
								/* The data-protection notice was a bare 80px scrolling iframe with
								   no label and no frame of its own, so it read as a rendering
								   fault rather than as a document you are being asked to read
								   before ticking the box below it. Same iframe, same source; it
								   is now named and sits in a bordered panel. */
								{xtype: "box", cls: "po-auth-terms", html:
									'<span class="po-auth-terms-label">Data protection</span>' +
									'<iframe id="dataProtection" src="conditions_iframe.html" title="Basic information about data protection"></iframe>'},
								{xtype: "checkboxfield", name: 'conditions', id: 'conditionsCheckbox', cls: 'po-auth-consent', allowBlank: false, submitValue: true, boxLabel: '<span>I have read the <a href="conditions.html" target="_blank" id="conditionsSignup">rules, conditions and privacy policy</a>.</span>'},
                                {xtype: "box", html: '<div class="formMessage" id="invalidSignUpMessage"></div>' +
                                            '<p data-guides="ignore" class="formActionRow"><a class="button btn-primary btn-form-action" href="javascript:void(0)" id="signUpButton"><i class="fa fa-user-plus" aria-hidden="true"></i> Create account</a></p>' +
                                            '<p class="po-auth-links"><a id="signUpBackLink" href="javascript:void(0)">Back to sign in</a></p>'
                                }
                            ]
                        },
                        {xtype: "container", itemId: "congratzPanel", hidden: true, cls: "po-auth-form", layout: {type: 'vbox', align: 'stretch'},
                            items: [
                                {xtype: "box", html: poAuthNotice('fa-envelope-o', 'Check your inbox', '')},
                                {xtype: "box", flex: 1, itemId: "messageBox", html: ''},
                                {xtype: "box", html: '<p data-guides="ignore" class="formActionRow"><a class="button btn-default btn-form-action" href="javascript:void(0)" id="signUpCloseButton"><i class="fa fa-check-circle-o" aria-hidden="true"></i> Close</a></p>'}
                            ]
                        }
                    ],
                    listeners: {
                        afterrender: function (cmp) {
                            poAttachPasswordReveal(cmp);
                            $("#signUpButton").click(function () {
                                me.signUpButtonClick();
                            });
                            $("#signUpCloseButton").click(function () {
                                me.signUpCloseButtonClick();
                            });
                            $("#signUpBackLink").click(function () {
                                me.signUpBackLinkClick();
                            });
                        }
                    }
                }
        );
        return this.component;
    };
    return this;
}
SignUpPanel.prototype = new View;


function ForgetPasswordPanel() {
    /*********************************************************************
     * ATTRIBUTES
     ***********************************************************************/
    this.name = "ForgetPasswordPanel";
    /*********************************************************************
     * OTHER FUNCTIONS
     ***********************************************************************/
    this.resetButtonClick = function () {
        this.getController().forgotPassButtonClickHandler(this);
    };
	this.forgetPasswordBackLinkClick = function () {
		this.getController().signInLinkClickHandler();
	};

    this.initComponent = function () {
        var me = this;
        this.component = Ext.widget(
                {xtype: "container", cls: "po-auth", layout: {type: 'vbox', align: 'stretch'}, flex: 1,
                  items: [
                          {xtype: 'form', itemId: "signInForm", flex: 1, border: 0, cls: "po-auth-form",
                              layout: {type: 'vbox', align: 'stretch'}, defaults: {labelAlign: "top", labelSeparator: "", border: false},
                              items: [
                                  {xtype: "box", html: poAuthHeader('Reset password', 'We will email you instructions for choosing a new one.')},
                                  {xtype: "textfield", name: 'userEmail', fieldLabel: 'Email address', vtype: 'email', emptyText: 'you@institution.org', value: Ext.util.Cookies.get('lastEmail'), allowBlank: false,
                                   		listeners: {
                                          specialkey: function (field, e) {
                                              if (e.getKey() === e.ENTER) {
                                                  me.resetButtonClick();
                                              }
                                          }
                                      }},
                                  {
                                      xtype: "box",html:
                                              '<div class="formMessage" id="invalidEmailMessage"></div>' +
                                              '<p data-guides="ignore" class="formActionRow"><a class="button btn-primary btn-form-action" href="javascript:void(0)" id="resetPassLink"><i class="fa fa-envelope-o" aria-hidden="true"></i> Send instructions</a></p>' +
                                              '<p class="po-auth-links"><a id="forgetPasswordBackLink" href="javascript:void(0)">Back to sign in</a></p>'
                                  }
                              ]
                          }
                      ]
              ,
              listeners: {
                  afterrender: function () {
                      $("#resetPassLink").click(function () {
                          me.resetButtonClick();
                      });

					  $("#forgetPasswordBackLink").click(function () {
                      	  me.forgetPasswordBackLinkClick();
					  });
                  }
              }
		}
        );
        return this.component;
    };
    return this;
}
ForgetPasswordPanel.prototype = new View;

function GuestSessionPanel(email, p) {
    /*********************************************************************
     * ATTRIBUTES
     ***********************************************************************/
    this.name = "SignInPanel";
    this.email = email;
    this.p = p;
    /*********************************************************************
     * OTHER FUNCTIONS
     ***********************************************************************/
    this.continueButtonClick = function () {
        application.getController("JobController").resetButtonClickHandler(null, true);
				location.reload();
    };

    this.initComponent = function () {
        var me = this;
        this.component = Ext.widget(
                {
                    xtype: "box", flex: 1, margin: '20px',
                    html:
                            '<div class="signInColumnDivider">' +
                            '<h2>Guest session</h2>' +
                            '<p>These are your temporary credentials. Save them &mdash; they are what you will use to resume a job or recover your data.</p>' +
                            '<h4><b>Email:</b> ' + me.email + '</h4>' +
                            '<h4><b>Password:</b> ' + me.p + '</h4>' +
                            '<p>Data, jobs and results belonging to guest users are kept for a maximum of <b>7 days</b>.</p>' +
                            '<p><a class="signUpLink" href="javascript:void(0)">Create an account</a> to keep them for longer. It takes a few seconds.</p>' +
                            '<p data-guides="ignore" class="formActionRow"><a class="button btn-primary btn-form-action" href="javascript:void(0)" id="continueButton"><i class="fa fa-arrow-right" aria-hidden="true"></i> Start working</a></p>' +
                            '</div>',
                    listeners: {
                        afterrender: function () {
                            $("#continueButton").click(function () {
                                me.continueButtonClick();
                            });
                        }
                    }
                }
        );
        return this.component;
    };

    return this;
}
GuestSessionPanel.prototype = new View;

function NoLoginSessionPanel(email, p) {
    /*********************************************************************
     * ATTRIBUTES
     ***********************************************************************/
    this.name = "SignInPanel";
    this.email = email;
    this.p = p;
    /*********************************************************************
     * OTHER FUNCTIONS
     ***********************************************************************/
    this.continueButtonClick = function () {
        application.getController("JobController").resetButtonClickHandler(null, true);
				location.reload();
    };

    this.initComponent = function () {
        var me = this;
        this.component = Ext.widget(
                {
                    xtype: "box", flex: 1, margin: '20px',
                    html:
                            '<div class="signInColumnDivider">' +
                            '<h2>No account</h2>' +
                            '<p>Write down your job ID. Without an account you have no data and jobs management area, so the ID is the only way back to your results.</p>' +
                            '<p>Data, jobs and results belonging to unregistered users are kept for a maximum of <b>7 days</b>.</p>' +
                            '<p><a class="signUpLink" href="javascript:void(0)">Create an account</a> to keep them for longer. It takes a few seconds.</p>' +
                            '<p data-guides="ignore" class="formActionRow"><a class="button btn-primary btn-form-action" href="javascript:void(0)" id="continueButton"><i class="fa fa-arrow-right" aria-hidden="true"></i> Start working</a></p>' +
                            '</div>',
                    listeners: {
                        afterrender: function () {
                            $("#continueButton").click(function () {
                                me.continueButtonClick();
                            });
							
							// TODO: force continue handler
							me.continueButtonClick();
                        }
                    }
                }
        );
        return this.component;
    };

    return this;
}
NoLoginSessionPanel.prototype = new View;

function ChangePasswordPanel() {
    /*********************************************************************
     * ATTRIBUTES
     ***********************************************************************/
    this.name = "ChangePasswordPanel";
    /*********************************************************************
     * OTHER FUNCTIONS
     ***********************************************************************/
    this.acceptButtonClick = function () {
        this.getController().changePasswordAcceptButtonClickHandler(this);
    };
    this.cancelButtonClick = function () {
        this.getController().changePasswordCancelButtonClickHandler(this);
    };

    this.showSuccessPanel = function () {
            var changePassForm = this.getComponent().queryById("changePassForm");
            var successPanel = this.getComponent().queryById("successPanel");
            changePassForm.setVisible(false);
            successPanel.setVisible(true);
            /* Same as showCongratzPanel: the window is the size of the form
               that was here a moment ago, and this is one sentence. */
            poResizeDialog(420, 280);
        };

    this.initComponent = function () {
        var me = this;
        this.component = Ext.widget(
            {xtype: "container", cls: "po-auth", layout: {type: 'vbox', align: 'stretch'}, flex: 1, maxWidth: 900, margin: '20',
                items: [
                    {xtype: 'form', itemId: "changePassForm", border: 0, cls: "po-auth-form", layout: {type: 'vbox', align: 'stretch'}, defaults: {labelAlign: "top", labelSeparator: "", border: false},
                        items: [
                            {xtype: "box", html: poAuthHeader('Change your password', 'You stay signed in on this device; other sessions are unaffected.')},
                            {xtype: "textfield", name: 'password', fieldLabel: 'New password', inputType: 'password', allowBlank: false},
                            {xtype: "textfield", name: 'password2', fieldLabel: 'Confirm new password', inputType: 'password', submitValue: false, allowBlank: false,
                                validator: function (value) {
                                    if ($("input[name=password]").val() != value) {
                                        return "Passwords do not match!";
                                    }
                                    return true;
                                }
                            },
                            {xtype: "box", html: '<p data-guides="ignore" class="formActionRow"><a class="button btn-primary btn-form-action" href="javascript:void(0)" id="acceptNewPassButton"><i class="fa fa-check" aria-hidden="true"></i> Save new password</a></p>' +
                                        '<p class="po-auth-links"><a id="cancelNewPassButton" href="javascript:void(0)">Cancel</a></p>'
                            }
                        ],
                    },
                    {xtype: "container", itemId: "successPanel", hidden: true, cls: "po-auth-form", layout: {type: 'vbox', align: 'stretch'},
                            items: [
                                {xtype: "box", html: poAuthNotice('fa-check-circle', 'Password changed', 'Your password has been successfully updated.')},
                                {xtype: "box", flex: 1, itemId: "messageBox", html: ''},
                                {xtype: "box", html: '<p data-guides="ignore" class="formActionRow"><a class="button btn-default btn-form-action" id="closeNewPassButton" href="javascript:void(0)"><i class="fa fa-arrow-circle-o-left" aria-hidden="true"></i> Close</a></p>'}
                            ]
                    }
                ],
                listeners: {
                    afterrender: function (cmp) {
                        poAttachPasswordReveal(cmp);
                        $("#acceptNewPassButton").click(function () {
                            me.acceptButtonClick();
                        });
                        $("#cancelNewPassButton").click(function () {
                            me.cancelButtonClick();
                        });
                        $("#closeNewPassButton").click(function () {
                            me.cancelButtonClick();
                        });
                    }
                }
            }
        );
        return this.component;
    };

    return this;
}
ChangePasswordPanel.prototype = new View;
