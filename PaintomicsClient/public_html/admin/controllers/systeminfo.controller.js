(function(){
	var app = angular.module('admin.controllers.systeminfo-controllers', [
		'ui.bootstrap',
		'ang-dialogs',
		'chart.js'
	]);

	app.controller('SystemInfoController', function($rootScope, $scope, $http, $dialogs, $state, $interval, APP_EVENTS) {

		/* True from a failed poll until the next good one: an outage shows one
		   error, not a new one every 3 s, and polling carries on so the charts
		   recover on their own once the server answers again. */
		var failing = false;

		this.retrieveSystemInfo = function(){
			$http($rootScope.getHttpRequestConfig("GET", "system-info", {})).
			then(
				function successCallback(response){
					failing = false;
					$scope.cpu_load = [response.data.cpu_use, 100 - response.data.cpu_use];
					$scope.mem_load = [response.data.mem_use, 100 - response.data.mem_use];
					$scope.swap_use = [response.data.swap_use, 100 - response.data.swap_use];
					$scope.disk_use = response.data.disk_use;
				},
				function errorCallback(response){
					console.error(response.data);
					var denied = response.data && /CredentialException/.test(response.data.message || "");
					/* No admin session will not fix itself between polls, so stop
					   polling and say how to fix it. Anything else may be a blip. */
					if (denied) {
						$rootScope.interval.forEach(function(i){ $interval.cancel(i); });
						$rootScope.interval = [];
					} else if (failing) {
						return;
					}
					failing = true;
					$dialogs.closeDialog();
					var message = denied
						? "Sign in to PaintOmics with an administrator account, then reload this page."
						: "Failed while retrieving the system information. The charts will update again once the server responds.";
					$dialogs.showErrorDialog(message, {
						logMessage : message + " at SystemInfoController:retrieveSystemInfo."
					});
				}
			);
		};

		this.sendCleanDatabasesRequest = function(){
			$dialogs.showWaitDialog("Cleaning the databases. This can take a few seconds.");
			$http($rootScope.getHttpRequestConfig("DELETE", "clean-databases", {})).
			then(
				function successCallback(response){
					$dialogs.closeDialog();
					$dialogs.showSuccessDialog("Databases cleaned.");
				},
				function errorCallback(response){
					$dialogs.closeDialog();
					var message = "Failed while cleaning databases.";
					$dialogs.showErrorDialog(message, {
						logMessage : message + " at SystemInfoController:sendCleanDatabasesRequest."
					});
					console.error(response.data);
				}
			);
		};
		//--------------------------------------------------------------------
		// INITIALIZATION
		//--------------------------------------------------------------------
		var me = this;

		this.retrieveSystemInfo();
		$rootScope.interval.push($interval(this.retrieveSystemInfo, 3000));

		$scope.cpu_load = [0, 100];
		$scope.cpu_options = {
			animation: {duration: 500},
			tooltip:{enabled:false},
			title: {display: true,text: 'CPU usage (%)'},
			maintainAspectRatio:false
		};
		$scope.mem_load = [0, 100];
		$scope.mem_options = {
			animation: {duration: 500},
			title: {display: true,text: 'Mem usage (%)'},
			tooltip:{enabled:false},
			maintainAspectRatio:false
		};
		$scope.swap_load = [0, 100];
		$scope.swap_options = {
			animation: {duration: 500},
			tooltip:{enabled:false},
			title: {display: true,text: 'Swap usage (%)'},
			maintainAspectRatio:false
		};

	});
})();
