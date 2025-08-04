export const submitFeedback = async (feedback: string, api: string) => {

    console.log("submitting feedback", feedback, api);

    // First, fetch the token from the API on the /check endpoint
    const check = await fetch(`${api}check`, {
        method: 'GET',
    });

    const checkData = await check.json();

    if (checkData.captcha) {
        window.alert("API is asking for a captcha, but this is not supported yet.");
        return;
    }

    if (!checkData.token) {
        window.alert("API error, please try again later.");
        return;
    }

    // Then, submit the feedback to the API on the /submit endpoint
    const deliver = await fetch(`${api}deliver`, {
        method: 'POST',
        body: JSON.stringify({ feedback, token: checkData.token })
    });

    const deliverData = await deliver.json();

    if (deliverData.success) {
        window.alert("Feedback submitted successfully");
    } else {
        window.alert("Feedback submission failed");
    }
}