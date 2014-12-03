;(function (define, undefined) {
    'use strict';
define([
    'jquery', 'underscore', 'js/edxnotes/views/edxnotes_visibility_decorator'
], function($, _, EdxnotesVisibilityDecorator) {
    return function (visibility, visibilityUrl) {
        var checkbox = $('p.action-inline > a.action-toggle-notes'),
            checkboxIcon = checkbox.children('i.checkbox-icon'),
            toggleNotes, sendRequest;

        toggleNotes = function () {
            if (visibility) {
                $('.edx-notes-wrapper').each(function () {
                    EdxnotesVisibilityDecorator.enableNote(this)
                });
                checkboxIcon.removeClass('icon-check-empty').addClass('icon-check');
            } else {
                EdxnotesVisibilityDecorator.disableNotes();
                checkboxIcon.removeClass('icon-check').addClass('icon-check-empty');
            }
        };

        sendRequest = function () {
            return $.ajax({
                type: 'PUT',
                url: visibilityUrl,
                dataType: 'json',
                data: JSON.stringify({'visibility': visibility}),
                error: function(response) {
                    console.log(JSON.parse(response.responseText));
                }
            });
        };

        checkbox.on('click', function (event) {
            visibility = !visibility;
            toggleNotes();
            sendRequest();
            event.preventDefault();
        });
    };
});
}).call(this, define || RequireJS.define);
