$(document).ready(function() {
    $('.test-downloader').click(function(e) {
        e.preventDefault();
        var downloaderBtn = $(this);
        var downloaderId = downloaderBtn.data('downloader-id');
        var originalHtml = downloaderBtn.html();
        
        console.log('Testing downloader:', downloaderId);
        
        // Show loading state
        downloaderBtn.html('<i class="fa fa-fw fa-spinner fa-spin"></i>').prop('disabled', true);
        
        // Get CSRF token
        var csrftoken = document.querySelector('[name=csrfmiddlewaretoken]')?.value || '';
        console.log('CSRF token:', csrftoken ? 'found' : 'not found');
        
        $.ajax({
            url: '/entities/api/test-downloader/' + downloaderId + '/',
            type: 'POST',
            headers: {
                'X-CSRFToken': csrftoken,
                'X-Requested-With': 'XMLHttpRequest'
            },
            success: function(response) {
                console.log('Success:', response);
                if (response.success) {
                    downloaderBtn.addClass('btn-success').removeClass('btn-info');
                    downloaderBtn.html('<i class="fa fa-fw fa-check"></i>');
                    if (response.message) {
                        downloaderBtn.attr('title', response.message);
                    }
                    setTimeout(function() {
                        downloaderBtn.removeClass('btn-success').addClass('btn-info');
                        downloaderBtn.html(originalHtml).prop('disabled', false);
                    }, 2000);
                } else {
                    downloaderBtn.addClass('btn-danger').removeClass('btn-info');
                    downloaderBtn.html('<i class="fa fa-fw fa-times"></i>');
                    var errorMsg = response.message || response.error || 'Connection failed';
                    downloaderBtn.attr('title', errorMsg);
                    console.error('Test failed:', errorMsg);
                    setTimeout(function() {
                        downloaderBtn.removeClass('btn-danger').addClass('btn-info');
                        downloaderBtn.html(originalHtml).prop('disabled', false);
                    }, 2000);
                }
            },
            error: function(xhr, status, error) {
                console.log('Error:', status, error, xhr.responseText);
                downloaderBtn.addClass('btn-danger').removeClass('btn-info');
                downloaderBtn.html('<i class="fa fa-fw fa-times"></i>');
                setTimeout(function() {
                    downloaderBtn.removeClass('btn-danger').addClass('btn-info');
                    downloaderBtn.html(originalHtml).prop('disabled', false);
                }, 2000);
            }
        });
    });
});
