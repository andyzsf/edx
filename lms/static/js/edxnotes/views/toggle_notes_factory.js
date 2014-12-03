;(function (define, undefined) {
'use strict';
define([
    'jquery', 'underscore', 'js/edxnotes/views/visibility_decorator'
], function($, _, EdxnotesVisibilityDecorator) {
    return function (visibility, visibilityUrl) {
        var checkbox = $('p.edx-notes-visibility > a.action-toggle-notes'),
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
            event.preventDefault();
            visibility = !visibility;
            toggleNotes();
            sendRequest();
        });
    };
});
}).call(this, define || RequireJS.define);
