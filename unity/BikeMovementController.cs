using UnityEngine;

public class BikeController : MonoBehaviour {
    [SerializeField] private CharacterController characterController;
    public GroundSensor groundSensor;

    [Header("Bike References")]
    [SerializeField] private GameObject bikeSteerSteel;

    [Header("VR Headset References")]
    [SerializeField] private Transform questControllerTransform;
    [SerializeField] private Transform cameraRig;

    [Header("Arduino Connectivity")]
    [SerializeField] private ArduinoSerialReader arduinoSerialReader;

    private float speed => arduinoSerialReader != null ? arduinoSerialReader.speed : 0f;

    [Header("Bike Stats")]
    public float speedMultiplier = 1f;
    public float turnSpeedModifier;

    float gravity = -9.81f;

    void Update() {
        cameraRig.forward = transform.forward;

        Vector3 move = groundSensor.isGrounded ?
            bikeSteerSteel.transform.forward :
            bikeSteerSteel.transform.forward + new Vector3(0,gravity,0);

        characterController.Move(move * speed * speedMultiplier * Time.deltaTime);

        Vector3 cross = Vector3.Cross(transform.forward, questControllerTransform.forward);

        if(speed > 0.3f)
            transform.rotation = Quaternion.Euler(0, cross.y * turnSpeedModifier * Time.deltaTime, 0);
    }
}
