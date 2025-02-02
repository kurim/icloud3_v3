
from ..global_variables     import GlobalVariables as Gb
from ..const                import (HOME, NOT_SET, HHMMSS_ZERO,
                                    EVLOG_ALERT,
                                    CRLF, CRLF_DOT,
                                    TOWARDS,
                                    FMF, FAMSHR,
                                    FMF_FNAME, FAMSHR_FNAME, IOSAPP,
                                    LATITUDE, LONGITUDE,
                                    LOCATION,
                                    )

from ..support              import start_ic3 as start_ic3
from ..support              import pyicloud_ic3_interface

from ..helpers.common       import (instr, is_statzone, )
from ..helpers.messaging    import (post_event, post_error_msg, post_monitor_msg,
                                    log_exception, log_start_finish_update_banner, log_rawdata,
                                    _trace, _traceha, )
from ..helpers.time_util    import (time_now_secs, secs_to_time, secs_since, secs_to, )
from .pyicloud_ic3          import (PyiCloudAPIResponseException, PyiCloud2FARequiredException, )


#<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>
#
#   Check the icloud Device to see if it qualified to be updated
#   on this polling cycle
#
#<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>
def any_reason_to_update_ic3_device_and_sensors(Device):
    """ See if there is a non-date reason to not update this device's info """

    Device.icloud_update_reason = ''
    Device.icloud_no_update_reason = ''

    # if Device.loc_data_latitude == 0 or Device.loc_data_longitude == 0:
    #     Device.icloud_update_reason = f"No Location Data, GPS-{Device.loc_data_fgps}"
    if Device.outside_no_exit_trigger_flag:
        Device.icloud_update_reason = 'Verify Location'
    elif Device.is_inzone and Device.next_update_time_reached is False:
        Device.icloud_no_update_reason = 'inZone & Next Update Time not Reached'
    elif Device.is_tracking_resumed:
        Device.icloud_update_reason = 'Resume Tracking using iCloud Location Services'
    elif Device.is_tracking_paused:
        Device.icloud_no_update_reason = 'Paused'
    elif Device.verified_flag is False:
        Device.icloud_no_update_reason = 'Not Verified'
    elif Gb.tracking_method_IOSAPP:
        Device.icloud_no_update_reason = 'Global iOS App Tracking Method'
    elif Device.is_tracking_method_IOSAPP:
        Device.icloud_no_update_reason = 'Device iOS App Tracking Method'
    elif (Gb.start_icloud3_inprocess_flag and Device.icloud_initial_locate_done is False):
        Device.icloud_no_update_reason = 'Start inProcess & initial locate not done'
    elif (Device.icloud_initial_locate_done is False
            and (Device.old_loc_poor_gps_cnt > 35
                or Device.count_discarded_update > 125)):
        Device.icloud_no_update_reason = 'Initial locate not done & poor cnt > 35'
    elif Device.icloud_initial_locate_done is False:
        pass
    elif (Device.sensor_zone == NOT_SET
            and Device.DeviceFmZoneHome.next_update_secs > Gb.this_update_secs):
        Device.icloud_no_update_reason = 'Zone NotSet Next Update Time not Reached'

    Device.icloud_update_needed_flag = (Device.icloud_no_update_reason == '')

    return Device.icloud_update_needed_flag

#<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>
#
#   Check the icloud device_tracker entity and UPDATE_TIME_trigger entity to
#   see if anything has changed and the icloud3 device_tracker entity should be
#   updated with the new location information.
#
#<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>
def should_ic3_device_and_sensors_be_updated(Device):
    try:
        Device.icloud_update_reason = ''
        Device.icloud_no_update_reason = ''
        Device.icloud_update_needed_flag   = False

        if (Device.sensor_zone == NOT_SET
                or Device.sensors[LATITUDE] == 0
                or Device.loc_data_latitude == 0
                or Device.icloud_initial_locate_done is False):
            Device.icloud_update_reason = f"Initial Locate@{Gb.this_update_time}"

        elif (Device.is_tracking_resumed):
            Device.icloud_update_reason = "Resuming via iCloud"

        elif Device.icloud_update_retry_flag:
            Device.icloud_update_reason  = "Retrying Location Refresh"

        elif Device.outside_no_exit_trigger_flag:
            Device.outside_no_exit_trigger_flag = False
            Device.icloud_update_reason = "Verify Location"

        elif (is_statzone(Device.loc_data_zone)
                and Device.loc_data_latitude  == Device.StatZone.base_latitude
                and Device.loc_data_longitude == Device.StatZone.base_longitude):
            Device.icloud_no_update_reason = "Stat Zone Base Location"

        elif Device.is_passthru_zone_delay_active:
            Device.icloud_no_update_reason = "Passing thru zone"

        # Data change older than the current data
        elif (Device.loc_data_secs < Device.last_update_loc_secs
                and Device.icloud_initial_locate_done):
            Device.icloud_update_reason = (f"Old Location-{Device.loc_data_time}")

        elif (Device.is_location_old_or_gps_poor and secs_to(Device.next_update_secs) <= 15):
            Device.icloud_update_reason = (f"Old Location-{Device.loc_data_time}")

        elif (Device.StatZone.timer_expired and Device.old_loc_poor_gps_cnt == 0):
            Device.icloud_update_reason = "Stationary Timer Reached"

        elif (Device.next_update_DeviceFmZone and Device.next_update_time_reached):
            Device.icloud_update_reason = f"Next Update Time Reached"
            if (Device.isnot_inzone and Device.next_update_DeviceFmZone.from_zone != HOME):
                Device.icloud_update_reason += (f" ({Device.next_update_DeviceFmZone.from_zone})")

        elif (Device.isnot_inzone
                and Device.loc_data_secs > Device.last_update_loc_secs + Device.old_loc_threshold_secs):
            Device.icloud_update_reason = "Newer Data is Available"

        Device.icloud_update_needed_flag = (Device.icloud_update_reason != '')

    except Exception as err:
        log_exception(err)
        Device.icloud_update_needed_flag = False

    return Device.icloud_update_needed_flag

#<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>
#
#   Get iCloud device & location info when using the
#
#<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>
def request_icloud_data_update(Device):
    '''
    Extract the data needed to determine location, direction, interval,
    etc. from the iCloud data set.

    Sample data set is:
        {'isOld': False, 'isInaccurate': False, 'altitude': 0.0, 'positionType': 'Wifi',
        'latitude': 27.72690098883266, 'floorLevel': 0, 'horizontalAccuracy': 65.0,
        'locationType': '', 'timeStamp': 1587306847548, 'locationFinished': True,
        'verticalAccuracy': 0.0, 'longitude': -80.3905776599289}
    '''
    if (Device.is_tracking_method_IOSAPP
            or Gb.PyiCloud is None):
        return

    devicename = Device.devicename

    try:
        if Device.icloud_update_reason:
            Device.display_info_msg("Requesting iCloud Location Update")

            Device.icloud_devdata_useable_flag = update_PyiCloud_RawData_data(Device)

            if (Device.icloud_devdata_useable_flag is False
                    and Device.icloud_initial_locate_done is False):
                Device.icloud_devdata_useable_flag = update_PyiCloud_RawData_data(Device)

            if Device.icloud_devdata_useable_flag is False:
                Device.display_info_msg("iCloud Location Not Available")
                if Gb.icloud_acct_error_cnt > 3:
                    error_msg = (f"iCloud3 Error > No Location Returned for {devicename}. "
                                    "iCloud may be down or there is an Authentication issue. ")
                    post_error_msg(Device.devicename, error_msg)

        return True

    except Exception as err:
        Device.icloud_acct_error_flag      = True
        Device.icloud_devdata_useable_flag = False

        # log_exception(err)
        error_msg = ("iCloud Location Request Error > An error occured refreshing the iCloud "
                        "Location. iCloud may be down or there is an internet connection "
                        f"issue. iCloud3 will try again later. ({err})")
        post_error_msg(Device.devicename, error_msg)

        return False

#----------------------------------------------------------------------------
def update_PyiCloud_RawData_data(Device, results_msg_flag=True):
    '''
    Refresh the location data for a device and all other dvices with the
    same tracking method.

    Input:  Device:     Device that wants to be updated. After getting the
                        data for that device, all other devices wth the
                        same trackin method are also updated since the data is available.
            results_msg_flag: True - Display the useability msg in the event_log
                        False - Do not display the results in the evemt_log.

    Return: True        The device data was updated successfully
            False       An api error occurred or no device data was retured returned
    '''

    try:
        if (Device.is_tracking_method_IOSAPP
                or Gb.PyiCloud is None):
            return False

        if Device.device_id_famshr is None and Device.device_id_fmf is None:
            return False

        if pyicloud_ic3_interface.is_authentication_2fa_code_needed(Gb.PyiCloud):
            if pyicloud_ic3_interface.authenticate_icloud_account(Gb.PyiCloud, called_from='data_handler') is False:
                return False

        if is_PyiCloud_RawData_data_useable(Device, results_msg_flag=False):
            return update_device_with_latest_raw_data(Device)

        famshr_ok, famshr_loc_time_ok, famshr_gps_ok, \
            famshr_secs, famshr_gps_accuracy, \
            famshr_time = _get_devdata_useable_status(Device, FAMSHR)
        fmf_ok, fmf_loc_time_ok, fmf_gps_ok, \
            fmf_secs, fmf_gps_accuracy, \
            fmf_time = _get_devdata_useable_status(Device, FMF)

        if ((famshr_ok and famshr_secs > fmf_secs)
                or fmf_ok):
            return update_all_devices_wih_latest_raw_data(Device)

        pyicloud_start_call_time = time_now_secs()

        # Do not refresh data for monitored device, will update it after refresh of another device
        if Device.is_monitored:
            pass

        # Refresh FamShr Data
        if Device.is_tracking_method_FAMSHR:
            if ((secs_since(Gb.pyicloud_refresh_time[FAMSHR]) >= 5
                    and  (famshr_secs != Device.loc_data_secs
                        or Device.next_update_secs > (famshr_secs + 5)))
                    or Device.icloud_initial_locate_done is False):

                Gb.PyiCloud.FamilySharing.refresh_client(requested_by_devicename=Device.devicename)


        # Refresh FmF Data
        if Device.is_tracking_method_FMF:
            if ((secs_since(Gb.pyicloud_refresh_time[FMF]) >= 5
                    and (fmf_secs != Device.loc_data_secs
                        or Device.next_update_secs > (fmf_secs + 5)))
                    or Device.icloud_initial_locate_done is False):

                Gb.PyiCloud.FindMyFriends.refresh_client(requested_by_devicename=Device.devicename)

        Gb.pyicloud_refresh_time[Device.tracking_method] = time_now_secs()
        Gb.pyicloud_location_update_cnt += 1
        Gb.pyicloud_calls_time += secs_since(pyicloud_start_call_time)

        if update_all_devices_wih_latest_raw_data(Device) is False:
            return False

        if is_PyiCloud_RawData_data_useable(Device, results_msg_flag=False) is False:
            return False

        if (Device.moved_since_last_update < .0005):
            return False

        return True

    except (PyiCloud2FARequiredException, PyiCloudAPIResponseException) as err:
        Device.icloud_acct_error_flag      = True
        Device.icloud_devdata_useable_flag = False

        error_msg = (f"{EVLOG_ALERT}iCloud Location Request Error > An error occured refreshing "
                        f"the iCloud Location Data. "
                        f"{CRLF_DOT}iCloud may be down, or"
                        f"{CRLF_DOT}iCloud Account needs to be reauthorized, or"
                        f"{CRLF_DOT}There may be an internet connection issue"
                        f"{CRLF}iCloud3 will try to access the iCloud Account again later. ({err})")
        post_event(Device.devicename, error_msg)
        post_error_msg(error_msg)

        return False

#----------------------------------------------------------------------------
def update_all_devices_wih_latest_raw_data(Device):
    update_device_with_latest_raw_data(Device, all_devices=True)

def update_device_with_latest_raw_data(Device, all_devices=False):
    '''
    Update a Device's location data with the latest data from FamSshr, FmF or the iOSApp
    if is is better or newer than the old data. Optionally, cycle thru all PyiCloud
    Devices and update the data for every device being tracked or monitored when
    new data is requested for a device since iCloud gives us data for all devices.

    Display a msg in the EvLog showing location times for all available data methods
    and the one selecetd.
    '''
    try:
        save_trace_prefix, Gb.trace_prefix = Gb.trace_prefix, "LOCDATA > "
        if all_devices:
            Update_Devices = Gb.Devices
            # log_start_finish_update_banner('start', Device.devicename, 'Update All Devices from RawData', '')
        else:
            Update_Devices = [Device]

        for _Device in Update_Devices:
            if _Device.PyiCloud_RawData:
                _RawData = _Device.PyiCloud_RawData

            elif _Device.PyiCloud_RawData_famshr and _Device.PyiCloud_RawData_fmf:
                _RawData = get_famshr_fmf_PyiCloud_RawData_to_use(_Device)
            else:
                continue


            # Make sure data is really a available
            try:
                latitude = _RawData.device_data[LOCATION][LATITUDE]
            except Exception as err:
                rawdata_msg = 'No Location data'
                if _RawData:
                    log_rawdata(f"{rawdata_msg}-{_Device.devicename}/{_Device.is_data_source_FAMSHR_FMF}",
                                {'filter': _RawData.device_data})
                continue
                # log_exception(err)

            requesting_device_flag = (_Device.devicename == Device.devicename)

            famshr_ok, famshr_loc_time_ok, famshr_gps_ok, \
                    famshr_secs, famshr_gps_accuracy, \
                    famshr_time = _get_devdata_useable_status(_Device, FAMSHR)
            fmf_ok, fmf_loc_time_ok, fmf_gps_ok, \
                    fmf_secs, fmf_gps_accuracy, \
                    fmf_time = _get_devdata_useable_status(_Device, FMF)

            # Add info for the Device that requested the update
            if is_PyiCloud_RawData_data_useable(Device, results_msg_flag=False) is False:
                if _Device is Device:
                    if (_Device.is_offline
                            # Beta 6-Added _RawData.gps_accuracy test
                            or _RawData.gps_accuracy > Gb.gps_accuracy_threshold
                            or (_Device.is_location_good
                                and _Device.is_data_source_FAMSHR_FMF
                                and _Device.loc_data_time > _RawData.location_time
                                and _Device.loc_data_gps_accuracy < _RawData.gps_accuracy)):

                        if (Device.old_loc_poor_gps_cnt % 5) == 2:
                            if Device.no_location_data:
                                reason_msg = f"No Location Data, "
                            else:
                                reason_msg = (  f"NewData-{_RawData.location_time}/±{_RawData.gps_accuracy:.0f}m "
                                                f"vs {_Device.loc_data_time_gps}, ")
                            event_msg =(f"Rejected  (#{Device.old_loc_poor_gps_cnt+1}) > "
                                        f"{reason_msg}"
                                        f"Updated-{_RawData.tracking_method} data, "
                                        f"{Device.device_status_msg}")
                            post_event(_Device.devicename, event_msg)

            if (_RawData is None
                    or _RawData.location_secs == 0 and _Device.iosapp_data_secs == 0
                    or _RawData.gps_accuracy > Gb.gps_accuracy_threshold):
                pass

            # Move the newest data from PyiCloud_RawData or the iOSApp data to the _Device's data fields
            # But, if there is a location error, select iCloud data so it will do another request
            elif (_RawData.location_secs >= _Device.iosapp_data_secs
                    or _Device.old_loc_poor_gps_cnt > 0):
                if _RawData.location_secs != _Device.loc_data_secs:
                    _Device.moved_since_last_update = \
                            _Device.distance_km(_RawData.device_data[LOCATION][LATITUDE],
                                                    _RawData.device_data[LOCATION][LONGITUDE])

                    # Move data from PyiCloud_RawData
                    _Device.update_dev_loc_data_from_raw_data_FAMSHR_FMF(_RawData,
                                                    requesting_device_flag=requesting_device_flag)

            elif _Device.iosapp_data_secs > 0:
                if _Device.iosapp_data_secs != _Device.loc_data_secs:
                    _Device.moved_since_last_update = \
                                _Device.distance_km(_Device.iosapp_data_latitude,
                                                    _Device.iosapp_data_longitude)

                    # Move data from iOS App
                    _Device.update_dev_loc_data_from_raw_data_IOSAPP()

            # The update data msg is only displayed when the requesting Device is updated so the msg
            # for the Device being updated was never displayed even though the data was updated
            # Display it now.
            elif requesting_device_flag and all_devices is False:
                Device.display_update_location_msg()

            # If Rejected msg being displayed, no need to display the Old Loc msg too
            if Device.old_loc_poor_gps_cnt >= 1:
                continue

            # Display appropriate message for the Device being updated or a monitor msg for all other Devices
            event_msg = 'Updated' if _Device.is_location_gps_good else 'Old Loc'
            event_msg += f" {_Device.dev_data_source}-{_Device.loc_data_time_gps}"
            other_times = ""
            if famshr_secs > 0 and Gb.tracking_method_FAMSHR_used and _Device.dev_data_source != 'FamShr':
                other_times += f"FamShr-{famshr_time}"
            if fmf_secs > 0 and Gb.tracking_method_FMF_used and _Device.dev_data_source != 'FmF':
                if other_times != "": other_times += ", "
                other_times += f"FmF-{fmf_time}"
            if _Device.iosapp_monitor_flag and _Device.dev_data_source != 'iOSApp':
                if other_times != "": other_times += ", "
                if _Device.iosapp_data_gps_accuracy > Gb.gps_accuracy_threshold:
                    other_times += f"iOSApp-{_Device.iosapp_data_time_gps}"
                else:
                    other_times += f"iOSApp-{_Device.iosapp_data_time}"
            if other_times:# != "":
                event_msg += f" ({other_times})"

            if _Device.is_offline:
                event_msg += f", DeviceStatus-{_Device.device_status}"

            if _Device.is_location_old_or_gps_poor and _Device.old_loc_poor_gps_cnt == 0:
                event_msg += f", UpdateIn-5 secs"

            if (requesting_device_flag and all_devices is False
                    and (_Device.is_location_gps_good or secs_to(_Device.next_update_secs <= 15))):
                post_event(_Device.devicename, event_msg)
            else:
                post_monitor_msg(_Device.devicename, event_msg)

        # if all_devices:
        #     log_start_finish_update_banner('finish', Device.devicename, 'Update All Devices from RawData-iOSApp', '')

        pyicloud_ic3_interface.reset_authentication_time(Gb.PyiCloud, 0)
        Gb.trace_prefix = save_trace_prefix

        return True

    except Exception as err:
        log_exception(err)

        return False

#----------------------------------------------------------------------------
def is_PyiCloud_RawData_data_useable(Device, results_msg_flag=True):
    '''
    Cycle thru the raw PyiCloud_RawData and see if the location times for all of the
    tracked devices is old.

    Rteurn:
        True - The data for the device is acceptible
        False - The data for Device is old
    '''

    famshr_ok, famshr_loc_time_ok, famshr_gps_ok, \
            famshr_secs, famshr_gps_accuracy, \
            famshr_time = _get_devdata_useable_status(Device, FAMSHR)
    fmf_ok, fmf_loc_time_ok, fmf_gps_ok, \
            fmf_secs, fmf_gps_accuracy, \
            fmf_time = _get_devdata_useable_status(Device, FMF)

    if famshr_ok or fmf_ok:
        is_useable_flag = True
        useable_msg     = 'Useable'
    elif famshr_loc_time_ok is False or fmf_loc_time_ok is False:
        is_useable_flag = False
        useable_msg     = 'Data-Old'
    elif famshr_gps_ok is False or fmf_gps_ok is False:
        is_useable_flag = False
        useable_msg     = 'Data-PoorGps'

    if results_msg_flag is False:
        return is_useable_flag

    data_type = 'iCloud'
    if famshr_secs >= fmf_secs:
        data_type = FAMSHR_FNAME
    elif fmf_secs > famshr_secs:
        data_type = FMF_FNAME

    event_msg = f"{data_type} {useable_msg} > "
    if famshr_secs > 0 and Gb.tracking_method_FAMSHR_used:
        event_msg += f"FamShr-{famshr_time}, "
    if fmf_secs > 0 and Gb.tracking_method_FMF_used:
        event_msg += f"FmF-{fmf_time}, "
    if is_useable_flag is False:
        event_msg += "Requesting New Location"

    if results_msg_flag:
        post_event(Device.devicename, event_msg)
    else:
        post_monitor_msg(Device.devicename, event_msg)
    return is_useable_flag

#----------------------------------------------------------------------------
def _get_devdata_useable_status(Device, tracking_method):
    '''
    Determine the useable status of the RawData location data. Check the time and the gps.

    Returns:
        time_useable_flag - the age of the location_secs is under the threshold,
        gps_useable_flag - the gps accuracy is under the threshold
        time_age_string - the formatted time 'hh:mm:ss (xxx ago)'
    '''

    loc_time_ok     = False
    gps_accuracy_ok = False
    loc_secs        = 0
    gps_accuracy    = 0
    time_str        = ''
    device_id       = None
    RawData         = None

    if tracking_method == FAMSHR:
        RawData = Device.PyiCloud_RawData_famshr
        device_id = Device.device_id_famshr
    elif tracking_method == FMF:
        RawData = Device.PyiCloud_RawData_fmf
        device_id = Device.device_id_fmf
    else:
        return False, False, False, 0, 0, ''

    if device_id is None or RawData is None:
        return False, False, False, 0, 0, ''

    loc_secs        = RawData.location_secs
    loc_time_ok     = secs_since(loc_secs) <= Device.old_loc_threshold_secs
    gps_accuracy_ok = RawData.is_gps_good
    gps_accuracy    = round(RawData.gps_accuracy)
    time_str        = f"{secs_to_time(loc_secs)}"
    if gps_accuracy_ok is False:
        time_str += f"#/±{gps_accuracy}m"
    useable_data    = (loc_time_ok and gps_accuracy_ok)

    return useable_data, loc_time_ok, gps_accuracy_ok, loc_secs, gps_accuracy, time_str

#----------------------------------------------------------------------------
def get_famshr_fmf_PyiCloud_RawData_to_use(_Device):
    '''
    Analyze tracking method and location times from the raw PyiCloud device data
    to get best data to use

    Return:
        _RawData - The PyiCloud_RawData (_famshr or _fmf) data object
    '''
    try:
        _RawData_famshr = _Device.PyiCloud_RawData_famshr
        _RawData_fmf    = _Device.PyiCloud_RawData_fmf

        # Is famshr raw data newer than fmf raw data
        if _RawData_famshr.location_secs >= _RawData_fmf.location_secs:
            _RawData = _RawData_famshr

        # Is fmf raw data newer than famshr raw data
        elif _RawData_fmf.location_secs >= _RawData_famshr.location_secs:
            _RawData = _RawData_fmf

        elif _RawData_famshr and _Device.is_tracking_method_FAMSHR:
            _RawData = _RawData_famshr

        elif _RawData_fmf and _Device.is_tracking_method_FMF:
            _RawData = _RawData_fmf

        elif _RawData_famshr:
            _RawData = _RawData_famshr

        elif _RawData_fmf:
            _RawData = _RawData_fmf

        else:
            error_msg = (f"{EVLOG_ALERT}Data Exception > {_Device.devicename} > No iCloud FamShr  "
                        f"or FmF Device Id was assigned to this device. This can be caused by "
                        f"No location data was returned from iCloud when iCloud3 was started."
                        f"{CRLF}Actions > Restart iCloud3. If the error continues, check the Event Log "
                        f"(iCloud3 Initialization Stage 2) and verify that the device is valid and a "
                        f"tracking method has been assigned. "
                        f"The device will be tracked by the iOS App.")
            post_event(error_msg)
            start_ic3.set_tracking_method(IOSAPP)

            _RawData = None

        error_msg = ''
        if _RawData.device_data is None:
            error_msg = 'No Device Data'
        elif LOCATION not in _RawData.device_data:
            error_msg = 'No Location Data'
        elif _RawData.device_data[LOCATION] == {}:
            error_msg = 'Location Data Empty'
        elif _RawData.device_data[LOCATION] is None:
            error_msg = 'Location Data Empty'
        elif _Device.is_tracking_paused:
            error_msg = 'Paused'

        if error_msg:
            if Gb.log_debug_flag:
                event_msg =(f"Location data not updated > {error_msg}, Will Retry")
                post_monitor_msg(_Device.devicename, event_msg)
            _RawData = None

        return _RawData

    except Exception as err:
        log_exception(err)
        return None
