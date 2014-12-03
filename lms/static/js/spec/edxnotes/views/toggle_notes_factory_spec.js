define([
    'annotator', 'js/edxnotes/views/edxnotes_visibility_decorator', 'jasmine-jquery'
], function(Annotator, VisibilityDecorator) {
    'use strict';
    describe('EdxNotes VisibilityDecorator', function() {
        var params = {
            endpoint: '/test_endpoint',
            user: 'a user',
            usageId : 'an usage',
            courseId: 'a course',
            token: 'test_token'
        };

        beforeEach(function() {
            loadFixtures('js/fixtures/edxnotes/edxnotes-wrapper.html');
            this.wrapper = document.getElementById('edx-notes-wrapper-123');
        });

        it('can initialize and reinitialize Notes if it should be visible', function() {
            var note = VisibilityDecorator.factory(this.wrapper, params, true);
            expect(note).toEqual(jasmine.any(Annotator));
            // can initialize Notes without parameters in second time.
            note = VisibilityDecorator.factory(this.wrapper);
            expect(note).toEqual(jasmine.any(Annotator));
            expect(annotator.options.store.prefix).toBe('/test_endpoint');
            expect(annotator.options.store.annotationData).toEqual({
                user: 'a user',
                usage_id : 'an usage',
                course_id: 'a course'
            });
        });

        it('does not initialize Notes if it should be not visible', function() {
            var note = VisibilityDecorator.factory(this.wrapper, params, false);
            expect(note).not.toBeDefined();
        });
    });
});
