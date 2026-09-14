//# sourceURL=MainView.js
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
* - MainView
*
*/
function MainView() {
	/*********************************************************************
	* ATTRIBUTES
	***********************************************************************/
	this.name = "MainView";

	this.subviews = {};
	this.currentView = null;

	/*********************************************************************
	* OTHER FUNCTIONS
	***********************************************************************/
	this.getSubView = function(aViewName) {
		return this.subviews[aViewName];
	};
	this.addMainView = function(aViewInstance) {
		this.subviews[aViewInstance.getName()] = aViewInstance;
	};
	this.setLoading = function(loading) {
		this.getComponent().queryById("mainViewCenterPanel").setLoading(loading);
	};

	this.getLastJobView = function() {
		for (var i = 5; i > 0; i--) {
			if (this.getSubView("PA_Step" + i + "JobView") !== undefined) {
				return this.getSubView("PA_Step" + i + "JobView");
			}
		}
		return null;
	};

	/**
	* Leaves the current job behind: every view it created is destroyed, not
	* just forgotten.
	*
	* removeAll() on the centre panel destroys what is inside the panel, and
	* only that. changeMainView() moves the outgoing view out of the panel with
	* remove(cmp, false) so a step can be returned to, which means that by the
	* time a user is looking at a pathway (Step 4) the Step 1, 2 and 3
	* components are all detached: rendered, alive, and not in anything
	* removeAll() sweeps. This method used to drop its references to them and
	* leave it there, so their `beforedestroy` listeners never ran. Step 3's is
	* the one that tears down the AI widget -- the widget lives on document.body,
	* outside the panel, precisely so it can stay visible over Step 4 -- so
	* leaving a job from the pathway view (wordmark -> "Yes") put the landing
	* page up with the AI launcher still in the corner, its status poll still
	* running against the abandoned job, and ~150 ExtJS components leaked per
	* reset. From Step 3 itself the same click worked, because there Step 3 was
	* the view inside the panel; that is what made the bug look intermittent.
	*
	* The component is read off the view directly rather than through
	* getComponent(), which instantiates a component that is still null: a
	* view that was registered but never shown has nothing to destroy.
	*/
	this.clearSubViews = function(){
		var centerPanel = this.getComponent().queryById("mainViewCenterPanel");
		for(var i in this.subviews){
			var view = this.subviews[i];
			if(view && view.getModel && view.getModel() !== null){
					var observers = view.getModel().getObservers();
					for(var j = observers.length - 1; j >= 0; j--) {
						view.getModel().deleteObserver(observers[j])
					}
			}

			nObservers = 0;

			var component = (view && view.component) ? view.component : null;
			if (component && !component.isDestroyed && typeof component.destroy === "function") {
				component.destroy();
			}

			delete this.subviews[i];
			this.subviews[i] = null;
		}
		this.currentView = null;
		this.subviews = {};
		// Anything else the panel holds. Every subview's component has already
		// removed itself from the panel on destroy, so this is a no-op for them
		// and a sweep for whatever was added to the panel outside the map.
		centerPanel.removeAll();
	};

	this.changeMainView = function(aViewName) {
		var aView = null;
		var me = this;
		if (aViewName === "" || (me.currentView !== null && me.currentView.getName() === aViewName)) {
			if (aViewName === "DM_MyDataListView") {
				me.currentView.updateContent();
			}
			return;
		}else if(aViewName === "contactForm"){
			application.getController("DataManagementController").sendReportHandler();
			return;
		} else if (this.subviews[aViewName] == null) {
			if (aViewName === "paintPathways") {
				application.getController("JobController").showJobInstance(this.getLastJobView().getModel()); //DELEGATE TO JobController
				return;
			} else if (aViewName === "DM_MyDataListView") {
				aView = new DM_MyDataListView();
				aView.setController(application.getController("DataManagementController"));
				this.subviews[aViewName] = aView;
			} else if (aViewName === "DM_MyDataUploadFilesPanel") {
				aView = new DM_MyDataUploadFilesPanel();
				aView.setController(application.getController("DataManagementController"));
				this.subviews[aViewName] = aView;
			} else if (aViewName === "fromBEDtoGenes") {
				aView = new DM_Bed2GenesJobView();
				aView.setController(application.getController("JobController"));
				this.subviews[aViewName] = aView;
			} else if (aViewName === "fromMiRNAtoGenes") {
				aView = new DM_miRNA2GenesJobView();
				aView.setController(application.getController("JobController"));
				this.subviews[aViewName] = aView;
			} else {
				aView = new DM_MyDataSubmitJobPanel(aViewName, application.getController("DataManagementController"));
				this.subviews[aViewName] = aView;
			}
		}else{
			aView = this.subviews[aViewName];
			if (aViewName === "DM_MyDataListView") {
				aView.updateContent();
			}
		}

		if (me.currentView !== null) {
			me.getComponent().queryById("mainViewCenterPanel").remove(me.currentView.getComponent(), false);
		}

		me.currentView = aView;

		me.getComponent().queryById("mainViewCenterPanel").add(aView.getComponent());

		// The contents rail belongs to the centre panel, not to the view that
		// filled it: buildAnalysisTOC inserts it into #mainViewCenterPanel, which
		// outlives every view swap. Only Step 3 and Step 4 ever built one, and
		// nothing tore it down, so leaving a job left its list of analyses -
		// Pathways selection, Metabolites Hub Analysis and the rest - pinned down
		// the side of the upload form, pointing at sections that were no longer on
		// the page.
		//
		// Rebuilding here rather than removing: buildAnalysisTOC drops any
		// existing rail, and when the new view has fewer than three sections to
		// list it also hands the reserved column back instead of leaving a gap.
		// Every view swap in the application comes through this method, including
		// the ones JobController makes between steps, so this is the one place
		// that sees them all. Step 3 and Step 4 still build their own afterwards -
		// their sections arrive on a delay and are not here yet at this point.
		//
		// paDeferFrame, not requestAnimationFrame: rAF is throttled to nothing in
		// a background tab, and every view swap in the application comes through
		// here. Reached with the tab hidden - which is what happens when a job
		// finishes while its author is reading something else - the rail was
		// never rebuilt, and Step 2 came up on a 1184px column where the same
		// page reached in the foreground uses 1114. See paDeferFrame in Util.js.
		paDeferFrame(function () {
			buildAnalysisTOC('#mainViewCenterPanel');
		});
	};

	this.showSignInDialog = function () {
		var loggedIn = Ext.util.Cookies.get("userID") !== null;

		if (Ext.util.Cookies.get("nologin") == null && loggedIn !== true) {
			var noLogin = true;
		} else {
			var noLogin = false;
		}

		/* Show the login form only when there is no session or nologin session enabled */
		if (loggedIn !== true && noLogin !== true) {
			$(".loggedOption").remove();
			application.getController("UserController").signInLinkClickHandler();
		}
	}

	this.initComponent = function() {
		var me = this;

		/* TODO: currently not used? Update it to work with "nologin" session */
		var sessionInfoBar = new SessionInfoBar();
		sessionInfoBar.setController(application.getController("UserController"));
		sessionInfoBar.getComponent().updateLoginState();

		var loggedIn = Ext.util.Cookies.get("userID") !== null;
		if (Ext.util.Cookies.get("nologin") == null && loggedIn !== true) {
			var noLogin = true;
		} else {
			var noLogin = false;
		}

		// The navigation used to be a `west` region: a 65px dark rail down the whole
		// left edge. It now renders inside the north region instead, so the same
		// markup and the same `.lateralMenu-body`/`.menuOption`/`.submenu` classes
		// are reused - only where it is mounted changed. The hover handler further
		// down still finds it by class.
		//
		// Each top-level label is wrapped in its own span so the header can drop
		// the words and keep the icons when the step actions need the room -- see
		// fitHeaderNav() below. The title attribute is what names the item once its
		// label is hidden, so it is load-bearing rather than decoration.
		//
		// Four pills, not six. Resources / Publications / Contact are read-once
		// links sharing a row with the step actions, and the nav lost that contest
		// on every results page: fitHeaderNav() reached `is-iconly` and stripped
		// all six labels at once, which is how the header came to read as an
		// undifferentiated cluster of glyphs. They now live behind one "More" item,
		// and the two remaining labels are shortened - "Personal storage" ->
		// "Storage", "Supporting tools" -> "Tools" - with the full name kept in
		// `title`. Together that gives back roughly 280px, enough for the words to
		// survive at the widths this is actually used at.
		var citeHTML = function(name, venue, year, doi, bib) {
			return "<li class='menuOption externalOption navPanel-cite'>" +
				"<a class='navPanel-cite-ref' href='" + doi + "' target='_blank' rel='noopener'>" +
				"<span class='navPanel-cite-name'>" + name + "</span>" +
				"<span class='navPanel-cite-meta'>" + venue + " &middot; " + year + "</span></a>" +
				"<a class='navPanel-cite-bib' href='resources/images/" + bib + "' target='_blank'>BibTeX</a></li>";
		};

		var navHTML = "<ul class='lateralMenu-body'>" +
				" <li class='menuOption' id='homeButton' title='Job view'><i class='fa fa-paint-brush'></i><span class='menuLabel'>Job view</span></li>" +
				" <li class='menuOption loggedOption' title='Personal storage'><i class='fa fa-cloud'></i><span class='menuLabel'>Storage</span>" +
				"  <ul class='submenu loggedOption'>" +
				(noLogin != true ?
					"     <li class='menuOption' data-name='DM_MyDataListView'><i class='fa fa-file-text'></i>  My files and Jobs</li>" +
					"     <li class='menuOption' data-name='DM_MyDataUploadFilesPanel'><i class='fa fa-cloud-upload'></i>   Upload new files</li>"
					:
					"     <li class='menuOption externalOption'><i class='fa fa-file-text'></i>  Only available for registered accounts.</li>"
				) +
				// "     <li class='menuOption' data-name='fileEdition'><i class='fa fa-cloud-upload'></i>   File edition</li>"+
				" </ul></li>" +
				" <li class='menuOption loggedOption' title='Supporting tools'><i class='fa fa-rocket'></i><span class='menuLabel'>Tools</span>" +
				" <ul class='submenu loggedOption'>" +
				"     <li class='menuOption' data-name='fromBEDtoGenes'><i class='fa fa-align-center'></i>   From Regions to Genes</li>" +
				"     <li class='menuOption' data-name='fromMiRNAtoGenes'><i class='fa fa-link'></i>   From miRNA to Genes</li>"+
				" </ul></li>" +
				/* One panel rather than three nested menus. Resources and the
				   citations are two lists of very different shape, so they get a
				   column each and Contact spans the foot; a "More" that flew out
				   three submenus sideways would have been a three-deep hover
				   chain, which no trackpad can hold open.

				   `.navPanel-inner` carries the grid, not the <ul>: jQuery's
				   fadeIn() writes `display: block` inline on the submenu itself,
				   which would beat any display the stylesheet set on it. */
				" <li class='menuOption' title='Resources, publications and contact'><i class='fa fa-ellipsis-h'></i><span class='menuLabel'>More</span>" +
				" <ul class='submenu navPanel'><li class='navPanel-inner'>" +
				"  <div class='navPanel-col'><h2 class='navPanel-title'>Resources</h2><ul class='navPanel-list'>" +
				"     <li class='menuOption externalOption'><a href='https://www.youtube.com/channel/UCSoQ3LSli9ZxOQTX56_WJeA' target='_blank' rel='noopener'><i class=\"fa fa-youtube-play\"></i>Tutorial video</a></li>" +
				/* The published site, not paintomics.readthedocs.io. That Read the
				   Docs project last built in July 2022 -- it has no webhook and
				   still points at the pre-rename repository -- so every one of the
				   app's five documentation links was sending users to a four-year-old
				   copy of the guide, on which the pages for both AI features 404.
				   The guide is now published from this repository by
				   .github/workflows/docs.yml on every push to master. */
				"     <li class='menuOption externalOption'><a href='https://conesalab.github.io/PaintOmics/' target='_blank' rel='noopener'><i class='fa fa-book'></i>Documentation</a></li>" +
				// The "PaintOmics 3" entry pointed at http://188.166.42.44/, a bare IP
				// that no longer answers at all (connection failure, not an error
				// page). Removed rather than repointed: there is no live PaintOmics 3
				// instance to send people to -- paintomics.org and paintomics.uv.es
				// both serve PaintOmics 4 -- and the PaintOmics 3 paper is already
				// linked under Publications. Restore this with a real URL if the
				// old release gets hosted again.
				// This server's own examples, not paintomics.uv.es's copy of a
				// 2017 archive: a deployment with its own datasets installed was
				// sending people elsewhere for data it does not use.
				"	  <li class='menuOption externalOption'><a href='" + SERVER_URL_EXAMPLE_DATASETS_DOWNLOAD + "'><i class='fa fa-download'></i>PaintOmics example data</a></li>" +
				"	  <li class='menuOption externalOption'><a href='https://paintomics.uv.es/resources/rgmatch_example_data.zip' target='_blank' rel='noopener'><i class='fa fa-download'></i>RGmatch example data</a></li>" +
				// RGmatch above stays an external link: it is a separate tool with
				// its own example data, not something this server's manifest
				// describes. miRNA2Genes is ours, so it comes from the manifest.
				"	  <li class='menuOption externalOption'><a href='" + SERVER_URL_EXAMPLE_DATASETS_DOWNLOAD + "?pipeline=mirna2genes'><i class='fa fa-download'></i>miRNA2Genes example data</a></li>" +
				"  </ul></div>" +
				/* The four entries used to be full citations set at `font-size:
				   9px` with a white heading inline - illegible on the dark rail
				   they were written for and invisible on the white bar that
				   replaced it. A citation nobody can read is not a citation, and
				   the two things a reader actually wants from this menu are the
				   paper and a BibTeX record. Both are one click away; the DOI page
				   carries the author list this was setting in 9px type. */
				"  <div class='navPanel-col'><h2 class='navPanel-title'>Cite PaintOmics</h2><ul class='navPanel-list'>" +
				citeHTML("PaintOmics 4", "Nucleic Acids Research", "2022", "https://doi.org/10.1093/nar/gkac352", "paintomics4.bib") +
				citeHTML("PaintOmics 3", "Nucleic Acids Research", "2018", "https://doi.org/10.1093/nar/gky466", "paintomics3.bib") +
				// The DOI, not the plain-http oxfordjournals.org address this used
				// to carry: that domain was retired into academic.oup.com years ago
				// and it was the last insecure link left in the navigation.
				citeHTML("PaintOmics 2", "Bioinformatics", "2011", "https://doi.org/10.1093/bioinformatics/btq594", "paintomics2-garcia-alcalde.bib") +
				citeHTML("RGmatch", "BMC Bioinformatics", "2016", "https://doi.org/10.1186/s12859-016-1293-1", "rgmatch.bib") +
				"  </ul></div>" +
				/* Kept a .menuOption inside a .submenu, and kept its data-name: the
				   boxready handler binds `.submenu .menuOption:not(.externalOption)`
				   as a descendant selector and marks `parents('.menuOption')`
				   selected, both of which still resolve through the extra wrappers
				   to the More pill. */
				"  <div class='navPanel-foot'><ul class='navPanel-list'>" +
				"     <li class='menuOption' data-name='contactForm'><i class='fa fa-envelope-o'></i>Contact by email</li>" +
				"  </ul></div>" +
				" </li></ul></li>" +
				"</ul>";

		this.component = Ext.create('Ext.container.Viewport', {
			id: 'mainView',
			border: false,
			defaults: {border: 0},
			layout: "border",
			items: [{
				xtype: "box",
				cls: "toolbar mainTopToolbar",
				region: 'north',
				html:
				'<div id="header">'+
				'  <img src="resources/images/paintomics-mark.svg" alt="PaintOmics AI">' +
				/* No version chip beside the wordmark. The header is the one row
				   in the application where two groups grow towards each other -
				   the nav pills from the left, the step actions from the right -
				   and fitHeaderNav() below already compacts the pills, and then
				   strips their labels, to keep them from colliding. A build
				   number sitting between the wordmark and the first pill spends
				   about 50px of the space that contest is fought over, to tell
				   the reader something no reader of a landing page is asking.
				   It is still in APP_VERSION for anything that needs it. */
				'  <h1>PaintOmics AI</h1>' +
				'</div>' +
				'<nav class="mainNav">' + navHTML + '</nav>' +
				/* The switch and the account button are one group now.

				   Both were direct children of the header's flex row, and the
				   switch alone carried `margin-left: auto` - so it, and only it,
				   absorbed the free space. That is what left a bare moon adrift
				   between the step actions and a labelled pill, belonging to
				   neither. The auto margin moves to this wrapper, which parks the
				   pair together at the end of the row and gives the header three
				   readable zones: brand and navigation, the actions for this step,
				   and the controls that are not about the analysis at all. */
				'<div class="headerUtilities">' +
				'  <button class="themeToggle" id="themeToggle" type="button" title="Switch between light and dark" aria-pressed="false"><i class="fa fa-moon-o"></i></button>' +
				'  <a class="button btn-sm btn-right loggedOption" data-name="logout" id="logoutButton" href="javascript:void(0)">' + (noLogin !== true ? '<i class="fa fa-sign-out" aria-hidden="true"></i> Log out' : '<i class="fa fa-sign-in" aria-hidden="true"></i> Sign in') + '</a>' +
				'</div>'
			}, {
				xtype: 'container', itemId: 'mainViewCenterPanel', id: 'mainViewCenterPanel',
				flex: 1, region: 'center', overflowY: "auto", style: "background-color:#f3f3f3;",
				defaults: {border: 0},
				items: []
			}],
			listeners: {
				boxready: function() {
					$("#logoutButton").click(function() {
						application.getController("UserController").signOutButtonClickHandler();
					});

					me.showSignInDialog();

					$('#homeButton').click(function() {
						$(".menuOption.selected").removeClass("selected");
						me.changeMainView('paintPathways');
					});

					$(".submenu .menuOption:not(.externalOption)").click(function() {
						$(".menuOption.selected").removeClass("selected");
						$(this).parents(".menuOption").addClass("selected");
						me.changeMainView(this.getAttribute("data-name"));
					});

					$(".lateralMenu-body").children(".menuOption").each(function() {
						var me = this;
						$(this).hover(function() {
							$(this).children(".submenu").fadeIn(100);
						}, function() {
							$(this).children(".submenu").fadeOut(0);
						});
					});

					$('#header').click(function() {
							application.getController("JobController").resetButtonClickHandler(null, false);
					});

					me.watchHeaderFit();
					me.initThemeToggle();

					if (Ext.util.Cookies.get("silence") != null) {
						console.log("Message already shown, ignoring.");
					} else {
						$.ajax({
							type: "POST",
							url: SERVER_URL_GET_MESSAGE,
							data: {message_type: "starting_message"},
							success: function (response) {
								if (response.success === false) {
									return;
								}
								showInfoMessage("Welcome to PaintOmics AI!", {
									message: "PaintOmics AI runs on <a href=\"https://aic.csic.es/supercomputador-drago/\" target=\"_blank\" rel=\"noopener\">Supercomputador Drago</a> (CSIC). For any inquiries, please contact us at <a href=\"mailto:paintomicsai@gmail.com\">paintomicsai@gmail.com</a>",
									showButton: true
								})
							},
							error: ajaxErrorHandler
						});
					}
				},
				resize: function(){
					if($("#mainViewCenterPanel").width() < 1000){
						$("#mainViewCenterPanel").addClass("mobileMode");
					}else{
						$("#mainViewCenterPanel").removeClass("mobileMode");
					}
					me.fitHeaderNav();
				}
			}

		});

		return this.component;
	};

	/* The light/dark switch.
	 *
	 * The theme itself is already resolved by the inline script in index.html,
	 * which runs before the first paint so nobody sees a white flash on the way
	 * to a dark interface. This only wires the control: it reflects whatever
	 * that script decided, and writes the user's choice down when they change it.
	 *
	 * Storing the choice is what makes it a choice. Until someone touches this
	 * button nothing is written, and the interface keeps following the operating
	 * system - including when that changes while the page is open.
	 */
	this.initThemeToggle = function () {
		var root = document.documentElement;
		var button = document.getElementById("themeToggle");
		if (!button) {
			return;
		}

		var reflect = function () {
			var dark = root.getAttribute("data-theme") === "dark";
			button.setAttribute("aria-pressed", dark ? "true" : "false");
			button.setAttribute("title", dark ? "Switch to light" : "Switch to dark");
			button.innerHTML = '<i class="fa ' + (dark ? "fa-sun-o" : "fa-moon-o") + '"></i>';
		};

		button.addEventListener("click", function () {
			var dark = root.getAttribute("data-theme") !== "dark";
			if (dark) {
				root.setAttribute("data-theme", "dark");
			} else {
				root.removeAttribute("data-theme");
			}
			try {
				window.localStorage.setItem("paintomics-theme", dark ? "dark" : "light");
			} catch (e) {
				// Private browsing refuses the write. The theme still applies for
				// this page; it just will not be remembered, which is the right
				// way round to fail.
			}
			reflect();
		});

		if (window.matchMedia) {
			var query = window.matchMedia("(prefers-color-scheme: dark)");
			var follow = function (ev) {
				var stored = null;
				try { stored = window.localStorage.getItem("paintomics-theme"); } catch (e) { /* private mode */ }
				if (stored !== null) {
					return;   // an explicit choice outranks the system
				}
				if (ev.matches) {
					root.setAttribute("data-theme", "dark");
				} else {
					root.removeAttribute("data-theme");
				}
				reflect();
			};
			if (query.addEventListener) {
				query.addEventListener("change", follow);
			} else if (query.addListener) {
				query.addListener(follow);   // Safari < 14
			}
		}

		reflect();
	};

	/* Keep the navigation clear of the step actions.
	 *
	 * The header holds two groups that grow towards each other: the nav pills
	 * from the left, and the step actions - Reset view / Sharing options / AI
	 * Interpret and, on Step 4, five of them - from the right. The step actions
	 * are `position: fixed` (main.css explains why: ExtJS writes inline
	 * left/width/top on the Step 4 container and only !important beats those), so
	 * they contribute nothing to the header's flex layout. The browser therefore
	 * has no way to know they are there, and the nav simply ran underneath them:
	 * on a 1459px window the pills ended 27px past the left edge of the AI
	 * Interpret button, which is what put that button on top of "Contact".
	 *
	 * Measuring is the only option while they are out of flow, so this measures.
	 * Two stages, in order, and never more than needed: tighten the pill padding
	 * first, and only drop the labels for the icons if that was not enough. Every
	 * item stays present and clickable in both states -- the title attribute set
	 * in navHTML above names it while the label is hidden -- which is the reason
	 * to compact the nav rather than clip it. Clipping cannot work here anyway:
	 * `overflow: hidden` on this list would cut off the dropdowns, which are
	 * absolutely positioned children of the items.
	 */
	this.fitHeaderNav = function() {
		var nav = document.querySelector(".lateralMenu-body");
		if (!nav) {
			return;
		}

		nav.classList.remove("is-compact", "is-iconly");

		// A step's toolbar reports "not on screen" as display:none, and the nav
		// should go back to full width for the landing page, which has none at
		// all. The test is getClientRects(), not offsetParent: offsetParent is
		// null for *every* position:fixed element whether it is visible or not,
		// so it read as hidden on the very screens this needs to measure.
		var actions = document.querySelector(".secondTopToolbar");
		var hasActions = !!(actions && actions.getClientRects().length > 0);

		/* Tell the stylesheet where the utility group starts.
		 *
		 * .secondTopToolbar is `position: fixed`, so it is offset from the right
		 * edge by hand, and that offset was the literal `178px` - 132 of account
		 * button, 30 of switch, 16 of gap, counted once and true only while all
		 * three stayed exactly that size. It was already wrong in one state the
		 * app reaches on its own: signing out removes the account button, and the
		 * band kept reserving room for it.
		 *
		 * Measured instead, from the group's own left edge, so the reservation
		 * follows whatever is actually in the corner. The divider is a ::before
		 * inside the group, so it is inside this measurement rather than another
		 * number to remember.
		 */
		var utilities = document.querySelector(".headerUtilities");
		if (utilities) {
			var edge = utilities.getBoundingClientRect().left;
			var reserved = Math.max(0, Math.ceil(document.documentElement.clientWidth - edge));
			document.documentElement.style.setProperty("--pa-header-utilities", reserved + "px");
		}
		// Nothing here decides whether the divider is drawn. It first hung on a
		// class written from this function, and inherited this watcher's staleness
		// with it: on a results page that settled without a final fit the hairline
		// simply never appeared, which is a visible defect for something whose
		// only job is to be seen. main.css answers the question declaratively with
		// :has(), so it cannot be stale.

		if (!hasActions) {
			return;
		}

		var bounds = actions.getBoundingClientRect();
		if (bounds.width === 0) {
			return;
		}

		// 12px so the two groups have a gap rather than merely not overlapping.
		var limit = bounds.left - 12;
		if (nav.getBoundingClientRect().right <= limit) {
			return;
		}

		nav.classList.add("is-compact");
		if (nav.getBoundingClientRect().right <= limit) {
			return;
		}

		nav.classList.add("is-iconly");
	};

	/* The step actions are built and torn down by each view as the user moves
	 * through the analysis, so the fit has to be rechecked when the header's
	 * contents change and not only when the window does. A MutationObserver on
	 * the body covers every one of the seven views that mount a toolbar without
	 * any of them having to know this exists.
	 *
	 * This used to carry a warning for anyone measuring it from an automated
	 * browser: run() was scheduled inside requestAnimationFrame, which Chrome
	 * pauses in a background tab, so a check that loaded a results page without
	 * bringing the tab to the front saw the nav at full width and overlapping the
	 * step actions. Instrumented at a 1200px viewport, a background tab logged
	 * zero fits; the same page in a foreground tab logged five, applied
	 * `is-compact`, and left a 22px gap.
	 *
	 * That was read as an artefact of how it was being measured. It was not - it
	 * was the same bug in the tab a reader is actually using. A results page is
	 * routinely opened and then left while its job finishes, and the nav that came
	 * back was the unfitted one; it healed only if the observer happened to fire
	 * again after the tab came forward, which returning to a settled page does not
	 * cause. Scheduled through paDeferFrame below, the fit runs either way.
	 */
	this.watchHeaderFit = function() {
		var me = this;
		var pending = null;
		var observer = null;
		var WATCHED = {
			childList: true, subtree: true, attributes: true,
			attributeFilter: ["class", "style"]
		};

		var run = function() {
			pending = null;
			// Detached while measuring. fitHeaderNav writes classes on the nav,
			// and an observer still attached would see its own effect, reschedule
			// itself, and spin once per frame for the life of the page.
			// Disconnecting also discards the records queued for those writes, so
			// reconnecting afterwards starts clean.
			if (observer) {
				observer.disconnect();
			}
			me.fitHeaderNav();
			if (observer) {
				observer.observe(document.body, WATCHED);
			}
		};

		var recheck = function() {
			if (pending) {
				return;
			}
			// Coalesced to the next frame: ExtJS mutates the DOM in bursts, and
			// measuring inside the burst would read a half-built toolbar. A frame
			// while the tab is visible, a timer while it is not - see the note
			// above, and paDeferFrame in Util.js.
			//
			// `pending` is a flag, not a handle: nothing here cancels a scheduled
			// run, and the two schedulers hand back ids that are not
			// interchangeable to cancel with.
			pending = true;
			paDeferFrame(run);
		};

		window.addEventListener("resize", recheck);
		if (window.MutationObserver) {
			observer = new MutationObserver(recheck);
			observer.observe(document.body, WATCHED);
		}
		recheck();
	};
}
MainView.prototype = new View;
